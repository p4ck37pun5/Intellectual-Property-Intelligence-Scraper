#!/usr/bin/env python3
"""
Intellectual Property Intelligence (IPI) Scraper — Core Python Logic
Reads input from stdin, scrapes USPTO/EPO/WIPO in parallel,
normalizes, optionally embeds, and writes JSON to stdout.
"""

import asyncio
import json
import sys
import logging
from typing import Any

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Lazy imports so startup is fast even if heavy deps missing
from scrapers.uspto import scrape_uspto
from scrapers.espacenet import scrape_espacenet
from scrapers.patentscope import scrape_patentscope
from normalizer import normalize_patent
from embedder import embed_patents


SCRAPER_MAP = {
    "US": scrape_uspto,
    "EP": scrape_espacenet,
    "WO": scrape_patentscope,
}


async def scrape_jurisdiction(query: str, jurisdiction: str, date_from: str, max_results: int) -> list[dict]:
    """Route a single query to the correct jurisdiction scraper."""
    scraper = SCRAPER_MAP.get(jurisdiction)
    if not scraper:
        log.warning(f"Unknown jurisdiction: {jurisdiction}")
        return []

    log.info(f"Scraping {jurisdiction} for '{query}'")
    try:
        results = await scraper(query=query, date_from=date_from, max_results=max_results)
        log.info(f"  → {jurisdiction}: {len(results)} raw records for '{query}'")
        return results
    except Exception as e:
        log.error(f"  ✗ {jurisdiction} scraper failed for '{query}': {e}")
        return []


async def main() -> None:
    # Read input from stdin (passed by main.js)
    raw = sys.stdin.read().strip()
    if not raw:
        log.error("No input received on stdin")
        print(json.dumps([]))
        return

    try:
        inp: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse input JSON: {e}")
        print(json.dumps([]))
        return

    queries: list[str] = inp.get("queries", [])
    jurisdictions: list[str] = inp.get("jurisdictions", ["US", "EP"])
    date_from: str = inp.get("date_from", "2020-01-01")
    max_results: int = int(inp.get("max_results", 100))
    want_embeddings: bool = bool(inp.get("embeddings", False))

    if not queries:
        log.warning("No queries provided — returning empty result")
        print(json.dumps([]))
        return

    # Cap per-query-per-jurisdiction to stay within max_results total
    per_task_limit = max(1, max_results // (len(queries) * len(jurisdictions)))

    # Build all tasks
    tasks = [
        scrape_jurisdiction(query, juri, date_from, per_task_limit)
        for query in queries
        for juri in jurisdictions
    ]

    log.info(f"Running {len(tasks)} scrape tasks in parallel…")
    raw_batches = await asyncio.gather(*tasks, return_exceptions=False)

    # Flatten
    all_raw: list[dict] = []
    for batch in raw_batches:
        if isinstance(batch, list):
            all_raw.extend(batch)

    log.info(f"Total raw records: {len(all_raw)}")

    # Normalize
    normalized: list[dict] = []
    for record in all_raw:
        try:
            n = normalize_patent(record)
            if n:
                normalized.append(n)
        except Exception as e:
            log.warning(f"Normalization error: {e}")

    log.info(f"Normalized: {len(normalized)} records")

    # Deduplicate by patent id
    seen: set[str] = set()
    deduped: list[dict] = []
    for p in normalized:
        pid = p.get("id", "")
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        deduped.append(p)

    log.info(f"After dedup: {len(deduped)} records")

    # Optional embeddings
    if want_embeddings:
        log.info("Generating embeddings…")
        try:
            deduped = await embed_patents(deduped)
        except Exception as e:
            log.error(f"Embedding failed: {e}")

    # Output
    print(json.dumps(deduped, ensure_ascii=False, default=str))
    log.info("Done — output written to stdout")


if __name__ == "__main__":
    asyncio.run(main())