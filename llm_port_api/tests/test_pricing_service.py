"""Unit tests for PricingService cost estimation."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from llm_port_api.services.gateway.pricing import CostEstimate, PriceCatalogEntry, PricingService


def _make_entry(
    provider: str = "openai",
    model: str = "gpt-4.1",
    input_price: str = "0.002",
    output_price: str = "0.008",
) -> PriceCatalogEntry:
    return PriceCatalogEntry(
        id=uuid.uuid4(),
        provider=provider,
        model=model,
        input_price_per_1k=Decimal(input_price),
        output_price_per_1k=Decimal(output_price),
        currency="USD",
    )


@pytest.fixture
def pricing_service() -> PricingService:
    service = PricingService()
    entries = [
        _make_entry("openai", "gpt-4.1", "0.002", "0.008"),
        _make_entry("anthropic", "claude-sonnet-4-20250514", "0.003", "0.015"),
        _make_entry("google", "gemini-2.5-pro", "0.00125", "0.01"),
    ]
    for entry in entries:
        service._cache[(entry.provider.lower(), entry.model.lower())] = entry
    return service


class TestResolve:
    """Price catalog resolution."""

    def test_exact_match(self, pricing_service: PricingService) -> None:
        entry = pricing_service.resolve("openai", "gpt-4.1")
        assert entry is not None
        assert entry.provider == "openai"

    def test_case_insensitive(self, pricing_service: PricingService) -> None:
        entry = pricing_service.resolve("OpenAI", "GPT-4.1")
        assert entry is not None
        assert entry.provider == "openai"

    def test_no_match(self, pricing_service: PricingService) -> None:
        entry = pricing_service.resolve("openai", "nonexistent-model")
        assert entry is None


class TestComputeCost:
    """Cost calculation logic."""

    def test_complete_estimate(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert est.status == "complete"
        # input: 1000/1000 * 0.002 = 0.002
        assert est.estimated_input_cost == Decimal("0.002")
        # output: 500/1000 * 0.008 = 0.004
        assert est.estimated_output_cost == Decimal("0.004")
        # total: 0.002 + 0.004 = 0.006
        assert est.estimated_total_cost == Decimal("0.006")
        assert est.price_catalog_id is not None
        assert est.currency == "USD"

    def test_large_token_count(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=1820,
            completion_tokens=930,
        )
        assert est.status == "complete"
        # input: 1820/1000 * 0.002 = 0.00364
        assert est.estimated_input_cost == Decimal("1820") / Decimal("1000") * Decimal("0.002")
        # output: 930/1000 * 0.008 = 0.00744
        assert est.estimated_output_cost == Decimal("930") / Decimal("1000") * Decimal("0.008")

    def test_partial_prompt_only(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=500,
            completion_tokens=None,
        )
        assert est.status == "partial"
        assert est.estimated_input_cost is not None
        assert est.estimated_output_cost is None
        assert est.estimated_total_cost is not None  # partial total = input only

    def test_partial_completion_only(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=None,
            completion_tokens=200,
        )
        assert est.status == "partial"
        assert est.estimated_input_cost is None
        assert est.estimated_output_cost is not None

    def test_no_tokens_with_known_price(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=None,
            completion_tokens=None,
        )
        assert est.status == "unavailable"
        assert est.estimated_total_cost is None
        assert est.price_catalog_id is not None  # price was found, just no tokens

    def test_unknown_model(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="unknown-model",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert est.status == "unavailable"
        assert est.estimated_total_cost is None
        assert est.price_catalog_id is None

    def test_no_provider(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name=None,
            model_alias="gpt-4.1",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert est.status == "unavailable"

    def test_no_model(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias=None,
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert est.status == "unavailable"

    def test_cached_tokens_reduces_input_cost(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=400,
        )
        assert est.status == "complete"
        # effective input: 1000 - 400 = 600
        # input cost: 600/1000 * 0.002 = 0.0012
        assert est.estimated_input_cost == Decimal("600") / Decimal("1000") * Decimal("0.002")

    def test_cached_tokens_exceeds_prompt(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=100,
            completion_tokens=500,
            cached_tokens=200,
        )
        assert est.status == "complete"
        # effective input: max(100 - 200, 0) = 0
        assert est.estimated_input_cost == Decimal("0")

    def test_zero_tokens(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="openai",
            model_alias="gpt-4.1",
            prompt_tokens=0,
            completion_tokens=0,
        )
        assert est.status == "complete"
        assert est.estimated_total_cost == Decimal("0")

    def test_anthropic_pricing(self, pricing_service: PricingService) -> None:
        est = pricing_service.compute_cost(
            provider_name="anthropic",
            model_alias="claude-sonnet-4-20250514",
            prompt_tokens=2000,
            completion_tokens=1000,
        )
        assert est.status == "complete"
        # input: 2000/1000 * 0.003 = 0.006
        assert est.estimated_input_cost == Decimal("0.006")
        # output: 1000/1000 * 0.015 = 0.015
        assert est.estimated_output_cost == Decimal("0.015")
        assert est.estimated_total_cost == Decimal("0.021")
