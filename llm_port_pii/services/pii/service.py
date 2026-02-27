"""Presidio-based PII detection and redaction service.

Wraps ``presidio-analyzer`` and ``presidio-anonymizer`` with a thin async
interface.  The heavy NLP model loading happens once at startup (via
``PIIService.create()``) so individual requests are fast.
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
    # Public API
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
