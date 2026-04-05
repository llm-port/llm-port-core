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
import logging
from dataclasses import dataclass, field
from typing import Any

from presidio_analyzer import AnalyzerEngine, RecognizerResult
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
    ) -> None:
        self._analyzer = analyzer
        self._anonymizer = anonymizer
        self._default_language = default_language
        self._default_score_threshold = default_score_threshold

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
        log.info("Presidio engines ready.")
        return cls(
            analyzer,
            anonymizer,
            default_language=default_language,
            default_score_threshold=default_score_threshold,
        )

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

        results: list[RecognizerResult] = await asyncio.to_thread(
            self._analyzer.analyze,
            text=text,
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

        results: list[RecognizerResult] = await asyncio.to_thread(
            self._analyzer.analyze,
            text=text,
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
                "Supported modes: 'redact', 'tokenize'."
            )

        lang = language or self._default_language
        ents = entities or DEFAULT_ENTITIES
        threshold = score_threshold or self._default_score_threshold

        all_entities: list[DetectedEntity] = []

        if mode == "tokenize":
            # Shared mutable state for building the token mapping.
            token_counters: dict[str, int] = {}
            token_mapping: dict[str, str] = {}
            # Cache: original text → token so repeated occurrences get
            # the same surrogate across the whole payload.
            value_to_token: dict[str, str] = {}

            async def _tokenize_text(text: str) -> str:
                results: list[RecognizerResult] = await asyncio.to_thread(
                    self._analyzer.analyze,
                    text=text,
                    language=lang,
                    entities=ents,
                    score_threshold=threshold,
                )
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
                sorted_results = sorted(non_overlapping, key=lambda r: r.start, reverse=True)
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
                """Analyze + redact a single text string."""
                results: list[RecognizerResult] = await asyncio.to_thread(
                    self._analyzer.analyze,
                    text=text,
                    language=lang,
                    entities=ents,
                    score_threshold=threshold,
                )
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

                engine_result: EngineResult = await asyncio.to_thread(
                    self._anonymizer.anonymize,
                    text=text,
                    analyzer_results=results,
                )
                return engine_result.text

            sanitize_fn = _redact_text

        # Deep-copy and walk the payload
        sanitized = dict(payload)

        # Chat completions: messages[].content
        if "messages" in sanitized:
            sanitized["messages"] = await self._walk_messages(
                sanitized["messages"], sanitize_fn,
            )

        # Embeddings: input (string | list[string])
        if "input" in sanitized:
            sanitized["input"] = await self._walk_input(
                sanitized["input"], sanitize_fn,
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
