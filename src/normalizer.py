"""
Patent Normalizer
Converts raw scraper output (any jurisdiction) → canonical output schema.

Output schema:
{
  "id":               str,          # e.g. "US11256551B2"
  "title":            str,
  "abstract":         str,
  "jurisdictions":    [str],        # e.g. ["US"]
  "filing_date":      str,          # ISO 8601 YYYY-MM-DD or ""
  "publication_date": str,
  "assignee":         str,
  "inventors":        [str],
  "classifications":  [str],        # CPC / IPC codes
  "source_url":       str,
  "embedding":        []            # populated by embedder.py
}
"""

import re
import logging
from typing import Any

log = logging.getLogger(__name__)


def normalize_patent(raw: dict) -> dict | None:
    """Dispatch to per-source normalizer based on _source tag."""
    source = raw.get("_source", "")
    try:
        if source == "USPTO":
            return _normalize_uspto(raw)
        elif source == "EPO":
            return _normalize_epo(raw)
        elif source == "WIPO":
            return _normalize_wipo(raw)
        else:
            log.debug(f"Unknown source '{source}' — using generic normalizer")
            return _normalize_generic(raw)
    except Exception as e:
        log.warning(f"Normalization error [{raw.get('patent_id', '?')}]: {e}")
        return None


# ─── USPTO ────────────────────────────────────────────────────────────────────

def _normalize_uspto(raw: dict) -> dict:
    patent_id = raw.get("patent_id") or raw.get("patent_number") or ""

    # Assignee: prefer org name, fall back to personal name
    assignees = _ensure_list(raw.get("assignees"))
    assignee  = _first_truthy(
        [a.get("assignee_organization") for a in assignees]
        + [
            f"{a.get('assignee_first_name', '')} {a.get('assignee_last_name', '')}".strip()
            for a in assignees
        ]
    )

    # Inventors
    inventors_raw = _ensure_list(raw.get("inventors"))
    inventors = [
        f"{i.get('inventor_first_name', '')} {i.get('inventor_last_name', '')}".strip()
        for i in inventors_raw
    ]
    inventors = [n for n in inventors if n]

    # CPC codes
    cpcs = _ensure_list(raw.get("cpcs"))
    classifications = [c.get("cpc_group", "") for c in cpcs if c.get("cpc_group")]

    return {
        "id":               patent_id,
        "title":            _clean(raw.get("patent_title")),
        "abstract":         _clean(raw.get("patent_abstract")),
        "jurisdictions":    ["US"],
        "filing_date":      _clean_date(raw.get("application_filing_date")),
        "publication_date": _clean_date(raw.get("patent_date")),
        "assignee":         assignee,
        "inventors":        inventors,
        "classifications":  classifications,
        "source_url":       f"https://patents.google.com/patent/{patent_id}",
        "embedding":        [],
    }


# ─── EPO ─────────────────────────────────────────────────────────────────────

def _normalize_epo(raw: dict) -> dict:
    country   = raw.get("_ep_country", "EP")
    number    = raw.get("_ep_number", "")
    kind      = raw.get("_ep_kind", "")
    patent_id = raw.get("patent_id") or f"{country}{number}{kind}"

    # Prefer enriched biblio fields, fall back to stub
    title     = _clean(raw.get("_ep_title") or raw.get("title"))
    abstract  = _clean(raw.get("_ep_abstract") or raw.get("abstract"))
    assignee  = raw.get("_ep_assignee", "")
    inventors = raw.get("_ep_inventors") or []
    cpcs      = [c for c in (raw.get("_ep_cpcs") or []) if c and c != "/"]

    return {
        "id":               patent_id,
        "title":            title,
        "abstract":         abstract,
        "jurisdictions":    [country or "EP"],
        "filing_date":      _clean_date(raw.get("_ep_filing_date")),
        "publication_date": _clean_date(raw.get("_ep_pub_date")),
        "assignee":         assignee,
        "inventors":        inventors,
        "classifications":  cpcs,
        "source_url": (
            f"https://worldwide.espacenet.com/patent/search/family/"
            f"{number}/publication/{patent_id}"
            if number else ""
        ),
        "embedding":        [],
    }


# ─── WIPO ─────────────────────────────────────────────────────────────────────

def _normalize_wipo(raw: dict) -> dict:
    patent_id = raw.get("patent_id") or raw.get("_wo_id", "")

    # Normalise WO prefix
    if patent_id and not patent_id.startswith("WO") and re.match(r"^\d", patent_id):
        patent_id = f"WO{patent_id}"

    link = raw.get("_wo_link", "")
    if not link and patent_id:
        link = f"https://patentscope.wipo.int/search/en/detail.jsf?docId={patent_id}"

    return {
        "id":               patent_id,
        "title":            _clean(raw.get("_wo_title") or raw.get("title")),
        "abstract":         _clean(raw.get("_wo_abstract") or raw.get("abstract")),
        "jurisdictions":    ["WO"],
        "filing_date":      _clean_date(raw.get("_wo_filing_date")),
        "publication_date": _clean_date(raw.get("_wo_pub_date")),
        "assignee":         raw.get("_wo_assignee", ""),
        "inventors":        raw.get("_wo_inventors") or [],
        "classifications":  raw.get("_wo_ipcs") or [],
        "source_url":       link,
        "embedding":        [],
    }


# ─── Generic fallback ─────────────────────────────────────────────────────────

def _normalize_generic(raw: dict) -> dict:
    return {
        "id":               raw.get("patent_id") or raw.get("id", ""),
        "title":            _clean(raw.get("title") or raw.get("patent_title")),
        "abstract":         _clean(raw.get("abstract") or raw.get("patent_abstract")),
        "jurisdictions":    [raw.get("_source", "XX")],
        "filing_date":      _clean_date(raw.get("filing_date")),
        "publication_date": _clean_date(raw.get("pub_date") or raw.get("publication_date")),
        "assignee":         raw.get("assignee", ""),
        "inventors":        raw.get("inventors") or [],
        "classifications":  raw.get("classifications") or [],
        "source_url":       raw.get("source_url", ""),
        "embedding":        [],
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clean(value: Any) -> str:
    """Strip HTML, collapse whitespace, return empty string on None."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", "", str(value))   # strip HTML tags
    text = re.sub(r"\s+", " ", text)             # collapse whitespace
    return text.strip()


def _clean_date(value: Any) -> str:
    """Normalise various date formats → YYYY-MM-DD or ''."""
    if not value:
        return ""
    value = str(value).strip()

    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value

    # YYYYMMDD (OPS uses this)
    if re.match(r"^\d{8}$", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

    # DD/MM/YYYY or DD.MM.YYYY
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", value)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"

    # Pull YYYY-MM-DD from any string (e.g. RFC 2822 "Tue, 15 Jan 2022 ...")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # "15 Jan 2022" style
    months = {
        "jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
        "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12",
    }
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", value)
    if m:
        mon = months.get(m.group(2).lower(), "00")
        return f"{m.group(3)}-{mon}-{m.group(1).zfill(2)}"

    return value  # return as-is if unrecognised


def _first_truthy(values: list) -> str:
    """Return first non-empty string in list."""
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _ensure_list(val: Any) -> list:
    """Ensure value is a list — wraps dicts, passes lists, handles None."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]