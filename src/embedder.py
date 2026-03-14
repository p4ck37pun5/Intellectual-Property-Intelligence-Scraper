"""
Patent Embedder
Generates 384-dim semantic embeddings using all-MiniLM-L6-v2.
Model is cached to /tmp after first download.

Embedding text = title + abstract (truncated to 512 tokens).
"""

import logging
import asyncio
from typing import Any

log = logging.getLogger(__name__)

# Model name — 384 dimensions, ~80MB download
MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE = 64
MAX_TEXT_CHARS = 2000  # Roughly 512 tokens


async def embed_patents(patents: list[dict]) -> list[dict]:
    """
    Generate embeddings for all patents.
    Runs model.encode() in a thread executor to keep the event loop free.
    """
    if not patents:
        return patents

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_sync, patents)


def _embed_sync(patents: list[dict]) -> list[dict]:
    """Synchronous embedding — runs in thread."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        log.error("sentence-transformers not installed. Run: pip install sentence-transformers")
        return patents

    log.info(f"Loading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME, cache_folder="/tmp/st_cache")

    texts = [_build_embed_text(p) for p in patents]

    log.info(f"Encoding {len(texts)} patents in batches of {BATCH_SIZE}…")
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    for patent, emb in zip(patents, embeddings):
        patent["embedding"] = emb.tolist()

    log.info("Embeddings generated")
    return patents


def _build_embed_text(patent: dict) -> str:
    """Combine title and abstract for embedding input."""
    title = (patent.get("title") or "").strip()
    abstract = (patent.get("abstract") or "").strip()

    combined = f"{title}. {abstract}" if title and abstract else (title or abstract)
    return combined[:MAX_TEXT_CHARS]