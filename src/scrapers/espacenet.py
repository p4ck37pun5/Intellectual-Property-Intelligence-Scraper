"""
EPO Espacenet Scraper — OPS REST API
Docs: https://ops.epo.org/3.2/rest-services/
Free: 4 req/s, 2500/week | Auth: 3,000,000 units/week (set EPO_KEY + EPO_SECRET)

Proxy:   jurisdiction="EP" → DE node via ProxyManager
Cache:   disk-based, 6h TTL, disabled in Apify production
Biblio:  second-pass fetch fills title/abstract/inventors/assignee
"""

import base64
import hashlib
import json
import logging
import os
import asyncio
import sys
from typing import Any

import httpx

_src = os.path.join(os.path.dirname(__file__), "..")
if _src not in sys.path:
    sys.path.insert(0, _src)

from proxy import proxy_manager
from cache import request_cache

log = logging.getLogger(__name__)

OPS_BASE   = "https://ops.epo.org/3.2/rest-services"
OPS_SEARCH = f"{OPS_BASE}/published-data/search"
OPS_BIBLIO = f"{OPS_BASE}/published-data/publication/epodoc/{{epodoc}}/biblio"

MAX_RETRIES   = 3
RETRY_DELAY   = 2.0
MAX_PAGE_SIZE = 100
# OPS biblio fetch: up to 100 docs per request (comma-separated epodoc list)
BIBLIO_BATCH  = 10


async def scrape_espacenet(query: str, date_from: str, max_results: int) -> list[dict]:
    """Scrape EPO via OPS API with proxy, cache, and biblio enrichment."""
    token = await _get_oauth_token()
    auth_headers = _auth_headers(token)
    date_filter  = date_from.replace("-", "")   # YYYYMMDD

    stub_results: list[dict] = []
    start = 1
    page_size = min(MAX_PAGE_SIZE, max_results)

    # ── Phase 1: search stubs (patent IDs) ──────────────────────────────────
    while len(stub_results) < max_results:
        cql    = _build_cql(query, date_filter)
        params = {
            "q":     cql,
            "Range": f"{start}-{start + page_size - 1}",
        }
        cache_key = f"epo_search:{hashlib.md5((cql + str(start)).encode()).hexdigest()}"
        data = request_cache.get(cache_key)

        if data is None:
            async with proxy_manager.client(jurisdiction="EP", headers=auth_headers) as client:
                data = await _fetch_json(client, OPS_SEARCH, params=params)
            if data:
                request_cache.set(cache_key, data)

        if not data:
            break

        batch = _parse_search_stubs(data)
        if not batch:
            break

        stub_results.extend(batch)
        log.debug(f"EPO search start={start}: got {len(batch)}, total={len(stub_results)}")

        if len(batch) < page_size:
            break

        start += page_size
        await asyncio.sleep(0.3)

    stub_results = stub_results[:max_results]

    # ── Phase 2: biblio enrichment (title, abstract, inventors, assignee) ───
    enriched = await _enrich_biblio(stub_results, auth_headers)

    for r in enriched:
        r["_source"]     = "EPO"
        r["_source_api"] = "OPS"

    return enriched


# ── CQL builder ───────────────────────────────────────────────────────────────

def _build_cql(query: str, date_filter: str) -> str:
    """
    Build CQL for OPS.
    Uses 'ti' (title) OR 'ab' (abstract) for broad coverage.
    Handles multi-word queries by quoting the phrase.
    """
    # Exact phrase if multi-word, else single term
    phrase = f'"{query}"' if " " in query.strip() else query.strip()
    return f"(ti={phrase} OR ab={phrase}) AND pd>={date_filter}"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth_headers(token: str | None) -> dict:
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get_oauth_token() -> str | None:
    """Obtain OAuth2 bearer token if EPO_KEY + EPO_SECRET env vars are set."""
    key    = os.getenv("EPO_KEY", "")
    secret = os.getenv("EPO_SECRET", "")
    if not key or not secret:
        log.debug("EPO: no credentials — using unauthenticated (2500 req/week limit)")
        return None

    cache_key = f"epo_token:{key[:8]}"
    cached = request_cache.get(cache_key)
    if cached:
        return cached.get("token")

    creds = base64.b64encode(f"{key}:{secret}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://ops.epo.org/3.2/auth/accesstoken",
                headers={
                    "Authorization":  f"Basic {creds}",
                    "Content-Type":   "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
            if token:
                # Cache for ~55 min (tokens expire at 60 min)
                request_cache.set(cache_key, {"token": token})
                log.info("EPO: OAuth token obtained")
            return token
    except Exception as e:
        log.warning(f"EPO OAuth failed: {e}")
        return None


# ── Response parsers ──────────────────────────────────────────────────────────

def _parse_search_stubs(data: dict) -> list[dict]:
    """Extract minimal patent stubs (id + country/number/kind) from OPS search."""
    try:
        refs = (
            data
            .get("ops:world-patent-data", {})
            .get("ops:biblio-search", {})
            .get("ops:search-result", {})
            .get("ops:publication-reference", [])
        )
        if isinstance(refs, dict):
            refs = [refs]

        stubs = []
        for ref in refs:
            doc_id  = ref.get("document-id", {})
            country = _text(doc_id.get("country"))
            number  = _text(doc_id.get("doc-number"))
            kind    = _text(doc_id.get("kind"))

            if not number:
                continue

            patent_id = f"{country}{number}{kind}" if country else number
            stubs.append({
                "patent_id":   patent_id,
                "_ep_country": country,
                "_ep_number":  number,
                "_ep_kind":    kind,
                "_ep_epodoc":  f"{country}{number}",
            })

        return stubs
    except Exception as e:
        log.warning(f"EPO search parse error: {e}")
        return []


def _parse_biblio(data: dict, epodoc: str) -> dict:
    """
    Extract title, abstract, inventors, assignees from OPS biblio response.
    Returns a dict of enrichment fields (merged into the stub).
    """
    enrichment: dict[str, Any] = {}
    try:
        docs = (
            data
            .get("ops:world-patent-data", {})
            .get("exchange-documents", {})
            .get("exchange-document", {})
        )
        if isinstance(docs, list):
            doc = docs[0]
        else:
            doc = docs

        bib = doc.get("bibliographic-data", {})

        # Title — prefer English
        titles = bib.get("invention-title", [])
        if isinstance(titles, dict):
            titles = [titles]
        for t in titles:
            if t.get("@lang", "") in ("en", "EN", ""):
                enrichment["_ep_title"] = _text(t) or ""
                break
        if "_ep_title" not in enrichment and titles:
            enrichment["_ep_title"] = _text(titles[0])

        # Abstract — prefer English
        abstracts = doc.get("abstract", [])
        if isinstance(abstracts, dict):
            abstracts = [abstracts]
        for ab in abstracts:
            if ab.get("@lang", "") in ("en", "EN", ""):
                paras = ab.get("p", [])
                if isinstance(paras, str):
                    enrichment["_ep_abstract"] = paras
                elif isinstance(paras, list):
                    enrichment["_ep_abstract"] = " ".join(
                        _text(p) for p in paras if p
                    )
                break

        # Parties
        parties = bib.get("parties", {})

        # Applicants (assignees)
        applicants = _normalise_list(parties.get("applicant", []))
        assignee_names = []
        for ap in applicants:
            name = _text(ap.get("applicant-name", {}).get("name"))
            if name:
                assignee_names.append(name)
        enrichment["_ep_assignee"] = assignee_names[0] if assignee_names else ""

        # Inventors
        inventors_raw = _normalise_list(parties.get("inventor", []))
        enrichment["_ep_inventors"] = [
            _text(inv.get("inventor-name", {}).get("name"))
            for inv in inventors_raw
            if _text(inv.get("inventor-name", {}).get("name"))
        ]

        # CPC classifications
        cpcs_raw = (
            bib.get("patent-classifications", {})
               .get("patent-classification", [])
        )
        if isinstance(cpcs_raw, dict):
            cpcs_raw = [cpcs_raw]
        enrichment["_ep_cpcs"] = [
            f"{_text(c.get('section'))}{_text(c.get('class'))}"
            f"{_text(c.get('subclass'))}{_text(c.get('main-group'))}"
            f"/{_text(c.get('subgroup'))}"
            for c in cpcs_raw
            if isinstance(c, dict)
        ]

        # Dates
        pub_info = bib.get("publication-reference", {}).get("document-id", {})
        enrichment["_ep_pub_date"] = _text(pub_info.get("date"))

        app_info = (
            bib.get("application-reference", {})
               .get("document-id", {})
        )
        enrichment["_ep_filing_date"] = _text(app_info.get("date"))

    except Exception as e:
        log.debug(f"EPO biblio parse error for {epodoc}: {e}")

    return enrichment


# ── Biblio enrichment phase ───────────────────────────────────────────────────

async def _enrich_biblio(stubs: list[dict], auth_headers: dict) -> list[dict]:
    """
    Fetch bibliographic details for each stub in batches of BIBLIO_BATCH.
    Merges enrichment fields into each stub.
    """
    if not stubs:
        return stubs

    enriched = list(stubs)  # copy

    for i in range(0, len(stubs), BIBLIO_BATCH):
        batch = stubs[i : i + BIBLIO_BATCH]
        epodocs = [s.get("_ep_epodoc", s["patent_id"]) for s in batch]
        epodoc_str = ",".join(epodocs)

        cache_key = f"epo_biblio:{hashlib.md5(epodoc_str.encode()).hexdigest()}"
        data = request_cache.get(cache_key)

        if data is None:
            url = OPS_BIBLIO.format(epodoc=epodoc_str)
            async with proxy_manager.client(jurisdiction="EP", headers=auth_headers) as client:
                data = await _fetch_json(client, url)
            if data:
                request_cache.set(cache_key, data)

        if not data:
            log.debug(f"EPO: no biblio data for batch {i//BIBLIO_BATCH + 1}")
            await asyncio.sleep(0.25)
            continue

        # Attempt per-document enrichment
        for j, stub in enumerate(batch):
            try:
                ep = stub.get("_ep_epodoc", stub["patent_id"])
                enrichment = _parse_biblio(data, ep)
                enriched[i + j].update(enrichment)
            except Exception as e:
                log.debug(f"EPO biblio merge error for {stub.get('patent_id')}: {e}")

        await asyncio.sleep(0.25)

    return enriched


# ── HTTP helper ───────────────────────────────────────────────────────────────

async def _fetch_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
) -> dict | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            log.warning(f"EPO HTTP {status} (attempt {attempt}/{MAX_RETRIES}) — {url[:70]}")
            if status == 403:
                log.error("EPO: Access denied. Set EPO_KEY + EPO_SECRET for authenticated access.")
                return None
            if status == 404:
                return None  # patent not found — skip silently
            if status == 429:
                wait = RETRY_DELAY * (2 ** attempt)
                log.info(f"EPO rate-limited — waiting {wait:.1f}s")
                await asyncio.sleep(wait)
            elif attempt == MAX_RETRIES:
                return None
            else:
                await asyncio.sleep(RETRY_DELAY * attempt)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            log.warning(f"EPO connection error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(RETRY_DELAY * attempt)
        except Exception as e:
            log.warning(f"EPO unexpected error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(RETRY_DELAY)

    return None


# ── Tiny helpers ──────────────────────────────────────────────────────────────

def _text(node: Any) -> str:
    """Extract text from an OPS JSON node (may be str, dict with '$', or None)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, dict):
        return str(node.get("$", node.get("#text", ""))).strip()
    return str(node).strip()


def _normalise_list(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


# ── Manual test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    async def _test():
        results = await scrape_espacenet("neural network", "2022-01-01", 5)
        print(f"Got {len(results)} results")
        for r in results:
            print(f"  {r.get('patent_id'):25} | {r.get('_ep_title', '')[:55]}")

    asyncio.run(_test())