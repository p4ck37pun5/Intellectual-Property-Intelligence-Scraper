"""
Shared utilities for the IPI Scraper.
"""

import hashlib
import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def make_stable_id(source: str, raw_id: str) -> str:
    """Generate a deterministic ID when the patent ID is missing or generic."""
    if raw_id and not raw_id.startswith(("WO_", "EP_unknown")):
        return raw_id
    h = hashlib.md5(f"{source}:{raw_id}".encode()).hexdigest()[:8]
    return f"{source}_{h}"


def deduplicate(patents: list[dict]) -> list[dict]:
    """
    Remove duplicate patents by ID.
    When two records share an ID, keep the one with more data.
    """
    seen: dict[str, dict] = {}
    for p in patents:
        pid = p.get("id", "")
        if not pid:
            continue
        if pid not in seen:
            seen[pid] = p
        else:
            # Prefer the record with longer abstract (more complete)
            if len(p.get("abstract", "")) > len(seen[pid].get("abstract", "")):
                seen[pid] = p
    return list(seen.values())


def truncate_text(text: str, max_chars: int = 5000) -> str:
    """Truncate text to max_chars, ending at a word boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    return (truncated[:last_space] + "…") if last_space > 0 else truncated + "…"


def clean_text(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_list(lst: list, size: int):
    """Yield successive chunks of `size` from `lst`."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def safe_get(d: dict, *keys: str, default: Any = "") -> Any:
    """Safely navigate nested dicts."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)
    return d