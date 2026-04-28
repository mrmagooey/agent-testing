"""Unit tests for CatalogPricingView."""

from __future__ import annotations

import pytest

from sec_review_framework.cost.pricing_view import CatalogPricingView, PricingView
from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_catalog(snapshots: dict) -> object:
    """Return a minimal catalog-like object whose snapshot() returns ``snapshots``."""

    class _FakeCatalog:
        def snapshot(self):
            return snapshots

    return _FakeCatalog()


def _make_snapshot(model_id: str, pricing) -> ProviderSnapshot:
    meta = ModelMetadata(
        id=model_id,
        raw_id=model_id,
        pricing=pricing,
    )
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset([model_id]),
        metadata={model_id: meta},
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_catalog_pricing_view_satisfies_protocol():
    """CatalogPricingView must satisfy the PricingView structural protocol."""
    catalog = _make_catalog({})
    view = CatalogPricingView(catalog)
    assert isinstance(view, PricingView)


# ---------------------------------------------------------------------------
# Happy path — OpenRouter-shaped pricing dict
# ---------------------------------------------------------------------------


def test_openrouter_string_pricing_parses_correctly():
    """OpenRouter ships prices as strings; they must be cast to float."""
    snap = _make_snapshot(
        "openrouter/meta-llama/llama-3.1-8b-instruct",
        {"prompt": "0.0000015", "completion": "0.000002"},
    )
    catalog = _make_catalog({"openrouter": snap})
    view = CatalogPricingView(catalog)

    result = view.get("openrouter/meta-llama/llama-3.1-8b-instruct")
    assert result is not None
    inp, out = result
    assert inp == pytest.approx(1.5e-6)
    assert out == pytest.approx(2.0e-6)


def test_float_pricing_values_accepted():
    """Probes that store floats directly should also work."""
    snap = _make_snapshot("provider/some-model", {"prompt": 1e-6, "completion": 3e-6})
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    result = view.get("provider/some-model")
    assert result == pytest.approx((1e-6, 3e-6))


# ---------------------------------------------------------------------------
# Alternative key names
# ---------------------------------------------------------------------------


def test_input_output_keys_accepted():
    """Alternative 'input'/'output' key names are also supported."""
    snap = _make_snapshot("some/model", {"input": "0.000001", "output": "0.000002"})
    catalog = _make_catalog({"x": snap})
    view = CatalogPricingView(catalog)

    result = view.get("some/model")
    assert result == pytest.approx((1e-6, 2e-6))


# ---------------------------------------------------------------------------
# Fallthrough / None cases
# ---------------------------------------------------------------------------


def test_missing_pricing_field_returns_none():
    """ModelMetadata with pricing=None must return None."""
    snap = _make_snapshot("no-price/model", pricing=None)
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    assert view.get("no-price/model") is None


def test_empty_pricing_dict_returns_none():
    """An empty dict has no recognizable keys; should return None."""
    snap = _make_snapshot("empty-price/model", pricing={})
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    assert view.get("empty-price/model") is None


def test_malformed_non_numeric_strings_return_none():
    """Non-numeric string values must not crash; return None."""
    snap = _make_snapshot("bad/model", {"prompt": "N/A", "completion": "free"})
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    assert view.get("bad/model") is None


def test_none_values_in_pricing_dict_return_none():
    """None values inside the pricing dict are not valid; return None."""
    snap = _make_snapshot("null-price/model", {"prompt": None, "completion": None})
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    assert view.get("null-price/model") is None


def test_partial_keys_return_none():
    """If only one of prompt/completion is present the entry is unusable."""
    snap = _make_snapshot("partial/model", {"prompt": "0.000001"})
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    assert view.get("partial/model") is None


def test_unknown_model_id_returns_none():
    """A model_id not present in any snapshot returns None."""
    snap = _make_snapshot("known/model", {"prompt": "0.000001", "completion": "0.000002"})
    catalog = _make_catalog({"p": snap})
    view = CatalogPricingView(catalog)

    assert view.get("unknown/model") is None


def test_empty_catalog_returns_none():
    """A catalog with no snapshots returns None."""
    catalog = _make_catalog({})
    view = CatalogPricingView(catalog)

    assert view.get("any/model") is None


# ---------------------------------------------------------------------------
# raw_id lookup (secondary lookup path)
# ---------------------------------------------------------------------------


def test_lookup_by_raw_id_when_key_differs():
    """If the snapshot's metadata key is different from model_id but raw_id matches."""
    meta = ModelMetadata(
        id="openrouter/anthropic/claude-3.5-sonnet",
        raw_id="openrouter/anthropic/claude-3.5-sonnet",
        pricing={"prompt": "0.000003", "completion": "0.000015"},
    )
    # Store under a different key to exercise the raw_id scan path.
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["openrouter/anthropic/claude-3.5-sonnet"]),
        metadata={"some-other-key": meta},
    )
    catalog = _make_catalog({"openrouter": snap})
    view = CatalogPricingView(catalog)

    result = view.get("openrouter/anthropic/claude-3.5-sonnet")
    assert result == pytest.approx((3e-6, 15e-6))
