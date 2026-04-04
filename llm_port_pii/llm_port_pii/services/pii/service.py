"""Presidio-based PII detection and redaction service.

Wraps ``presidio-analyzer`` and ``presidio-anonymizer`` with a thin async
interface.  The heavy NLP model loading happens once at startup (via
``PIIService.create()``) so individual requests are fast.

Supports PII **redaction** — replacing detected entities with
placeholder tags (e.g. ``<PERSON>``).  Reversible tokenization
and response de-tokenization are available in the **PII Pro** module.
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

        Replaces detected PII with entity-type tags (e.g. ``<PERSON>``).

        Walks ``messages[].content`` (string or multimodal array) and the
        ``input`` field (for embeddings).  All other fields are forwarded
        unchanged.

        .. note::

           Reversible tokenization (``mode="tokenize"``) and response
           de-tokenization are available in the **PII Pro** module.
        """
        if mode != "redact":
            raise ValueError(
                f"Unsupported sanitize mode '{mode}'. "
                "Core PII supports 'redact' only. "
                "Use the PII Pro module for tokenize/detokenize."
            )

        lang = language or self._default_language
        ents = entities or DEFAULT_ENTITIES
        threshold = score_threshold or self._default_score_threshold

        all_entities: list[DetectedEntity] = []

        async def _sanitize_text(text: str) -> str:
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

        # Deep-copy and walk the payload
        sanitized = dict(payload)

        # Chat completions: messages[].content
        if "messages" in sanitized:
            sanitized["messages"] = await self._walk_messages(
                sanitized["messages"], _sanitize_text,
            )

        # Embeddings: input (string | list[string])
        if "input" in sanitized:
            sanitized["input"] = await self._walk_input(
                sanitized["input"], _sanitize_text,
            )

        return SanitizeResult(
            payload=sanitized,
            pii_report=all_entities,
            token_mapping=None,
            entities_found=len(all_entities),
        )

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
