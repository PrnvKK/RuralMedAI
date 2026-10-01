"""
Shared singleton embedding model for the clinical coding pipeline.

Both ICDCodingService and ProcedureCodingService import `get_embedder()` and
`encode_with_progress()`. The model is loaded exactly once per process.
"""

from __future__ import annotations

import logging
from typing import List

import numpy as np

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_embedder = None


def get_embedder():
    """Return the shared SentenceTransformer instance (loads on first call)."""
    global _embedder
    if _embedder is None:
        logger.info("Loading shared embedding model: %s …", _EMBEDDING_MODEL)
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer(_EMBEDDING_MODEL)
        logger.info("Shared embedding model ready.")
    return _embedder


def encode_with_progress(texts: List[str], batch_size: int = 512, label: str = "Encoding") -> np.ndarray:
    """
    Encode texts in batches and log progress via logger.info() every 10 batches.
    Uses logger instead of tqdm so progress is visible in Docker logs in real time.
    """
    embedder = get_embedder()
    total = len(texts)
    n_batches = (total + batch_size - 1) // batch_size
    chunks: List[np.ndarray] = []

    for batch_idx in range(n_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, total)
        batch_embs = embedder.encode(
            texts[start:end],
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        chunks.append(batch_embs)
        # Log every 10 batches and on completion
        if batch_idx % 10 == 0 or end == total:
            pct = int((end / total) * 100)
            logger.info("  %s: %3d%% (%d / %d)", label, pct, end, total)

    return np.vstack(chunks)
