"""
Integration tests for scrapers — hit real APIs.
Skipped automatically in CI unless RUN_INTEGRATION=1 is set.

Run locally:
    RUN_INTEGRATION=1 pytest tests/test_scrapers.py -v -s
"""

import sys
import os
import asyncio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

INTEGRATION = os.getenv("RUN_INTEGRATION", "0") == "1"
skip_integration = pytest.mark.skipif(
    not INTEGRATION,
    reason="Set RUN_INTEGRATION=1 to run live API tests",
)


# ─── USPTO ────────────────────────────────────────────────────────────────────

@skip_integration
def test_uspto_returns_results():
    from scrapers.uspto import scrape_uspto

    results = asyncio.run(scrape_uspto(
        query="blockchain",
        date_from="2022-01-01",
        max_results=10,
    ))

    assert isinstance(results, list), "Expected a list"
    assert len(results) > 0, "Expected at least 1 result"
    assert results[0].get("_source") == "USPTO"
    assert results[0].get("patent_id"), "Expected patent_id"

    print(f"\n  USPTO: {len(results)} results, sample id={results[0].get('patent_id')}")


@skip_integration
def test_uspto_pagination():
    from scrapers.uspto import scrape_uspto

    results = asyncio.run(scrape_uspto(
        query="machine learning",
        date_from="2021-01-01",
        max_results=150,
    ))

    assert len(results) > 100, "Expected >100 results (requires pagination)"


# ─── EPO ──────────────────────────────────────────────────────────────────────

@skip_integration
def test_epo_returns_results():
    from scrapers.espacenet import scrape_espacenet

    results = asyncio.run(scrape_espacenet(
        query="neural network",
        date_from="2022-01-01",
        max_results=5,
    ))

    assert isinstance(results, list)
    # EPO may return empty if unauthenticated rate limit hit — that's acceptable
    print(f"\n  EPO: {len(results)} results")
    for r in results[:2]:
        print(f"    {r.get('patent_id')} — source={r.get('_source')}")


# ─── WIPO ─────────────────────────────────────────────────────────────────────

@skip_integration
def test_wipo_returns_results():
    from scrapers.patentscope import scrape_patentscope

    results = asyncio.run(scrape_patentscope(
        query="CRISPR",
        date_from="2021-01-01",
        max_results=10,
    ))

    assert isinstance(results, list)
    print(f"\n  WIPO: {len(results)} results")
    for r in results[:2]:
        print(f"    {r.get('patent_id')} — {r.get('_wo_title', '')[:60]}")


# ─── Normalizer round-trip ────────────────────────────────────────────────────

@skip_integration
def test_full_pipeline_us():
    """Scrape → normalize → check schema compliance."""
    from scrapers.uspto import scrape_uspto
    from normalizer import normalize_patent

    REQUIRED_FIELDS = [
        "id", "title", "abstract", "jurisdictions",
        "filing_date", "publication_date", "assignee",
        "inventors", "classifications", "source_url", "embedding",
    ]

    raw = asyncio.run(scrape_uspto("quantum", "2022-01-01", 5))
    assert raw, "USPTO returned no raw results"

    normalized = [normalize_patent(r) for r in raw]
    normalized = [n for n in normalized if n]
    assert normalized, "Normalization returned nothing"

    for field in REQUIRED_FIELDS:
        assert field in normalized[0], f"Missing field: {field}"

    print(f"\n  Pipeline: {len(raw)} raw → {len(normalized)} normalized")
    print(f"  Sample: {normalized[0]['id']} | {normalized[0]['title'][:50]}")