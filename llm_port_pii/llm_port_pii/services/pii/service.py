"""Presidio-based PII detection and redaction service.

Wraps ``presidio-analyzer`` and ``presidio-anonymizer`` with a thin async
interface.  The heavy NLP model loading happens once at startup (via
``PIIService.create()``) so individual requests are fast.

Supports PII **redaction** — replacing detected entities with
placeholder tags (e.g. ``<PERSON>``) — and **tokenization** — replacing
detected entities with reversible surrogate tokens (e.g. ``[PERSON_1]``)
that preserve semantic meaning for the LLM while hiding real values.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import EngineResult

log = logging.getLogger(__name__)

# Default PII entity types Presidio should look for.
DEFAULT_ENTITIES: list[str] = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
    "LOCATION",
    "DATE_TIME",
    "NRP",
    "MEDICAL_LICENSE",
    "URL",
]

# Supported language codes configured for PII detection.
SUPPORTED_LANGUAGES: list[str] = ["en", "de", "es", "zh"]


@dataclass
class DetectedEntity:
    """A single PII entity detected in the text."""

    entity_type: str
    start: int
    end: int
    score: float
    text: str


@dataclass
class ScanResult:
    """Result of a PII scan operation."""

    entities: list[DetectedEntity] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return len(self.entities) > 0


@dataclass
class RedactResult:
    """Result of a PII redaction operation."""

    original_text: str
    redacted_text: str
    entities_found: int


@dataclass
class SanitizeResult:
    """Result of sanitizing an OpenAI-shaped payload."""

    payload: dict[str, Any]
    pii_report: list[DetectedEntity]
    token_mapping: dict[str, str] | None
    entities_found: int


class PIIService:
    """Facade over Presidio analyzer + anonymizer engines."""

    def __init__(
        self,
        analyzer: AnalyzerEngine,
        anonymizer: AnonymizerEngine,
        *,
        default_language: str = "en",
        default_score_threshold: float = 0.35,
        analysis_cache_size: int = 20_000,
    ) -> None:
        self._analyzer = analyzer
        self._batch = BatchAnalyzerEngine(analyzer_engine=analyzer)
        self._anonymizer = anonymizer
        self._default_language = default_language
        self._default_score_threshold = default_score_threshold
        self._analyses = _AnalysisCache(analysis_cache_size)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        default_language: str = "en",
        default_score_threshold: float = 0.35,
    ) -> PIIService:
        """Create service instance; loads spaCy model (slow, do once)."""
        log.info("Initializing Presidio engines (loading spaCy model)...")
        analyzer = AnalyzerEngine()
        anonymizer = AnonymizerEngine()
        service = cls(
            analyzer,
            anonymizer,
            default_language=default_language,
            default_score_threshold=default_score_threshold,
        )
        # The first analysis finishes setting up spaCy's pipeline: the first
        # chat after a start waited ~0.9 s for it. Pay it here, at startup.
        service._batch.analyze_iterator(
            texts=["Warm-up: John Smith lives in Berlin."],
            language=default_language,
        )
        log.info("Presidio engines ready.")
        return service

    # ------------------------------------------------------------------
    # Public API -- raw text
    # ------------------------------------------------------------------

    async def scan(
        self,
        text: str,
        *,
        language: str | None = None,
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> ScanResult:
        """Detect PII entities in *text*."""
        lang = language or self._default_language
        ents = entities or DEFAULT_ENTITIES
        threshold = score_threshold or self._default_score_threshold

        (results,) = await self._analyze_many(
            [text],
            language=lang,
            entities=ents,
            score_threshold=threshold,
        )

        detected = [
            DetectedEntity(
                entity_type=r.entity_type,
                start=r.start,
                end=r.end,
                score=round(r.score, 4),
                text=text[r.start : r.end],
            )
            for r in results
        ]
        return ScanResult(entities=detected)

    async def redact(
        self,
        text: str,
        *,
        language: str | None = None,
        entities: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> RedactResult:
        """Detect and redact PII entities in *text*."""
        lang = language or self._default_language
        ents = entities or DEFAULT_ENTITIES
        threshold = score_threshold or self._default_score_threshold

        (results,) = await self._analyze_many(
            [text],
            language=lang,
            entities=ents,
            score_threshold=threshold,
        )

        engine_result: EngineResult = await asyncio.to_thread(
            self._anonymizer.anonymize,
            text=text,
            analyzer_results=results,
        )

        return RedactResult(
            original_text=text,
            redacted_text=engine_result.text,
            entities_found=len(results),
        )

    # ------------------------------------------------------------------
    # Public API -- OpenAI-shaped payloads
    # ------------------------------------------------------------------

    async def sanitize_payload(
        self,
        payload: dict[str, Any],
        *,
        mode: str = "redact",
        language: str | None = None,
        entities: list[str] | None = None,
        score_threshold: float | None = None,
        token_mapping: dict[str, str] | None = None,
    ) -> SanitizeResult:
        """Sanitize all text-bearing fields in an OpenAI-shaped payload.

        Walks ``messages[].content`` (string or multimodal array) and the
        ``input`` field (for embeddings).  All other fields are forwarded
        unchanged.

        Modes
        -----
        ``redact``
            Replaces detected PII with entity-type tags
            (e.g. ``<PERSON>``).
        ``tokenize``
            Replaces detected PII with reversible surrogate tokens
            (e.g. ``[PERSON_1]``) and returns a ``token_mapping`` dict
            so the caller can restore originals after the LLM response.
        """
        if mode not in ("redact", "tokenize"):
            raise ValueError(
                f"Unsupported sanitize mode '{mode}'. "
                "Supported modes: 'redact', 'tokenize'.",
            )

        lang = language or self._default_language
        ents = entities or DEFAULT_ENTITIES
        threshold = score_threshold or self._default_score_threshold

        all_entities: list[DetectedEntity] = []

        # Every text of the payload analysed at once, in the order the walk
        # below visits them: what was seen before comes from the cache, the
        # rest goes through spaCy in one batch. Analysed one by one, each
        # message cost a spaCy run and a thread hop -- about 15 ms apiece, on
        # the whole chat history, again on every turn.
        analyses = iter(
            await self._analyze_many(
                self._texts_of(payload),
                language=lang,
                entities=ents,
                score_threshold=threshold,
            ),
        )

        if mode == "tokenize":
            # Shared mutable state for building the token mapping.
            # Continued from an earlier call when given one: a value it has
            # keeps its token, and new tokens are numbered after its own.
            token_mapping = dict(token_mapping or {})
            value_to_token = {value: token for token, value in token_mapping.items()}
            token_counters = _counters(token_mapping)

            async def _tokenize_text(text: str) -> str:
                results = next(analyses)
                for r in results:
                    all_entities.append(
                        DetectedEntity(
                            entity_type=r.entity_type,
                            start=r.start,
                            end=r.end,
                            score=round(r.score, 4),
                            text=text[r.start : r.end],
                        ),
                    )
                if not results:
                    return text

                # Remove overlapping results: when two spans overlap,
                # keep the one with the higher score (or larger span
                # as tiebreaker). Sort by score desc, then span size desc.
                results_sorted = sorted(
                    results,
                    key=lambda r: (r.score, r.end - r.start),
                    reverse=True,
                )
                non_overlapping: list[RecognizerResult] = []
                for r in results_sorted:
                    if not any(
                        r.start < kept.end and r.end > kept.start
                        for kept in non_overlapping
                    ):
                        non_overlapping.append(r)

                # Sort by start position descending so we can
                # replace from end to start without shifting offsets.
                sorted_results = sorted(
                    non_overlapping, key=lambda r: r.start, reverse=True,
                )
                chars = list(text)
                for r in sorted_results:
                    original = text[r.start : r.end]
                    if original in value_to_token:
                        token = value_to_token[original]
                    else:
                        count = token_counters.get(r.entity_type, 0) + 1
                        token_counters[r.entity_type] = count
                        token = f"[{r.entity_type}_{count}]"
                        value_to_token[original] = token
                        token_mapping[token] = original
                    chars[r.start : r.end] = list(token)
                return "".join(chars)

            sanitize_fn = _tokenize_text
        else:
            token_mapping = None  # type: ignore[assignment]

            async def _redact_text(text: str) -> str:
                """Redact a single text string, from its analysis."""
                results = next(analyses)
                for r in results:
                    all_entities.append(
                        DetectedEntity(
                            entity_type=r.entity_type,
                            start=r.start,
                            end=r.end,
                            score=round(r.score, 4),
                            text=text[r.start : r.end],
                        ),
                    )
                if not results:
                    return text

                # Inline: replacing spans is cheap next to the analysis, and
                # a thread hop per message is what this change removes.
                return self._anonymizer.anonymize(
                    text=text, analyzer_results=results,
                ).text

            sanitize_fn = _redact_text

        # Deep-copy and walk the payload
        sanitized = dict(payload)

        # Chat completions: messages[].content
        if "messages" in sanitized:
            sanitized["messages"] = await self._walk_messages(
                sanitized["messages"],
                sanitize_fn,
            )

        # Embeddings: input (string | list[string])
        if "input" in sanitized:
            sanitized["input"] = await self._walk_input(
                sanitized["input"],
                sanitize_fn,
            )

        return SanitizeResult(
            payload=sanitized,
            pii_report=all_entities,
            token_mapping=token_mapping or None,
            entities_found=len(all_entities),
        )

    async def detokenize_payload(
        self,
        payload: dict[str, Any],
        token_mapping: dict[str, str],
    ) -> dict[str, Any]:
        """Restore original PII values in an OpenAI-shaped response payload.

        Walks ``choices[].message.content`` and replaces surrogate tokens
        (e.g. ``[PERSON_1]``) with their original values using the mapping
        returned from a prior ``sanitize_payload(mode='tokenize')`` call.
        """
        if not token_mapping:
            return payload

        def _replace_tokens(text: str) -> str:
            result = text
            for token, original in token_mapping.items():
                result = result.replace(token, original)
            return result

        sanitized = dict(payload)

        # Chat completion response: choices[].message.content
        choices = sanitized.get("choices")
        if isinstance(choices, list):
            new_choices: list[dict[str, Any]] = []
            for choice in choices:
                if not isinstance(choice, dict):
                    new_choices.append(choice)
                    continue
                new_choice = dict(choice)
                message = choice.get("message")
                if isinstance(message, dict):
                    new_message = dict(message)
                    content = message.get("content")
                    if isinstance(content, str):
                        new_message["content"] = _replace_tokens(content)
                    new_choice["message"] = new_message
                new_choices.append(new_choice)
            sanitized["choices"] = new_choices

        return sanitized

    # ------------------------------------------------------------------
    # Private helpers -- OpenAI schema walkers
    # ------------------------------------------------------------------

    async def _analyze_many(
        self,
        texts: list[str],
        *,
        language: str,
        entities: list[str],
        score_threshold: float,
    ) -> list[list[RecognizerResult]]:
        """The analysis of each of *texts*, in order.

        Remembered per text and settings: a chat sends its whole history on
        every turn, and each message's analysis is the same as the last time.
        The texts not seen before go through spaCy together, in one batch and
        one thread hop.
        """
        settings_key = (language, tuple(sorted(entities)), score_threshold)
        keys = [
            (hashlib.sha256(t.encode("utf-8")).digest(), settings_key) for t in texts
        ]
        missing: dict[tuple[Any, ...], str] = {}
        for key, text in zip(keys, texts, strict=True):
            if text and key not in self._analyses and key not in missing:
                missing[key] = text
        found: dict[tuple[Any, ...], tuple[tuple[str, int, int, float], ...]] = {}
        if missing:
            batch = await asyncio.to_thread(
                self._batch.analyze_iterator,
                texts=list(missing.values()),
                language=language,
                batch_size=len(missing),
                entities=entities,
                score_threshold=score_threshold,
            )
            for key, results in zip(missing, batch, strict=True):
                found[key] = tuple(
                    (r.entity_type, r.start, r.end, r.score) for r in results
                )
                self._analyses.put(key, found[key])
        analyses: list[list[RecognizerResult]] = []
        for key, text in zip(keys, texts, strict=True):
            spans = found.get(key) or (self._analyses.get(key) if text else ())
            analyses.append(
                [
                    RecognizerResult(entity_type=e, start=b, end=f, score=sc)
                    for e, b, f, sc in spans
                ],
            )
        return analyses

    @staticmethod
    def _texts_of(payload: dict[str, Any]) -> list[str]:
        """The texts ``sanitize_payload`` rewrites, in the order it visits them."""
        texts: list[str] = []
        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    texts.extend(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
        input_value = payload.get("input")
        if isinstance(input_value, str):
            texts.append(input_value)
        elif isinstance(input_value, list):
            texts.extend(item for item in input_value if isinstance(item, str))
        return texts

    @staticmethod
    async def _walk_messages(
        messages: Any,
        sanitize_fn: Any,
    ) -> list[dict[str, Any]]:
        """Walk OpenAI messages array, sanitizing all text content."""
        if not isinstance(messages, list):
            return messages
        result: list[dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                result.append(msg)
                continue
            new_msg = dict(msg)
            content = msg.get("content")
            if isinstance(content, str):
                new_msg["content"] = await sanitize_fn(content)
            elif isinstance(content, list):
                # Multimodal content array (text + image + file parts)
                new_parts: list[Any] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_val = part.get("text", "")
                        new_part = dict(part)
                        new_part["text"] = await sanitize_fn(text_val)
                        new_parts.append(new_part)
                    else:
                        # Image, audio, file parts -- pass through
                        new_parts.append(part)
                new_msg["content"] = new_parts
            result.append(new_msg)
        return result

    @staticmethod
    async def _walk_input(
        input_value: Any,
        sanitize_fn: Any,
    ) -> Any:
        """Walk embeddings input field (string or list of strings)."""
        if isinstance(input_value, str):
            return await sanitize_fn(input_value)
        if isinstance(input_value, list):
            result: list[Any] = []
            for item in input_value:
                if isinstance(item, str):
                    result.append(await sanitize_fn(item))
                else:
                    result.append(item)
            return result
        return input_value


class _AnalysisCache:
    """The most recent analyses, by text hash and scan settings.

    Holds entity types, offsets and scores -- never the text itself.
    """

    def __init__(self, size: int) -> None:
        self._size = max(size, 0)
        self._entries: OrderedDict[
            tuple[Any, ...], tuple[tuple[str, int, int, float], ...],
        ] = OrderedDict()

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def get(self, key: tuple[Any, ...]) -> tuple[tuple[str, int, int, float], ...]:
        self._entries.move_to_end(key)
        return self._entries[key]

    def put(
        self, key: tuple[Any, ...], value: tuple[tuple[str, int, int, float], ...],
    ) -> None:
        if not self._size:
            return
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._size:
            self._entries.popitem(last=False)


def _counters(token_mapping: dict[str, str]) -> dict[str, int]:
    """The highest number used per entity type in *token_mapping*."""
    counters: dict[str, int] = {}
    for token in token_mapping:
        entity, _, number = token.strip("[]").rpartition("_")
        if entity and number.isdigit():
            counters[entity] = max(counters.get(entity, 0), int(number))
    return counters
