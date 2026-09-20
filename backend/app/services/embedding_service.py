"""
embedding_service.py
--------------------
Local, free, private text embeddings (fastembed / ONNX, no GPU, no API key). Contract text is
embedded ON THIS MACHINE and never sent anywhere.

  EMBEDDING_PROVIDER=local   BAAI/bge-small-en-v1.5 (384-d). ~67 MB, downloaded once on first use.
  EMBEDDING_PROVIDER=none    retrieval falls back to full-text search only.

Failure is never fatal: if the model cannot be loaded (offline first run, missing wheel) the
service reports itself unavailable, retrieval uses full-text search alone, and it retries later.
"""

import asyncio
import hashlib
import logging
import math
import re
import time
from typing import Callable, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

RETRY_AFTER_S = 600
_model = None
_failed_at: Optional[float] = None
_last_error: Optional[str] = None
_lock = asyncio.Lock()


# ─── test double (APP_ENV=test only) ──────────────────────────────────────────

# Words a person would treat as "about the same thing". This lets tests prove semantic retrieval
# finds a passage that shares NO words with the question. It says nothing about the real model.
_CONCEPTS = [
    {"expire", "expires", "expiry", "expiration", "end", "ends", "lapse", "lapses", "until", "terminate", "cancel", "conclude"},
    {"pay", "payment", "fee", "fees", "cost", "price", "invoice", "owe", "charge"},
    {"secret", "confidential", "confidentiality", "private", "disclose", "nda"},
    {"law", "governing", "jurisdiction", "court", "courts", "venue"},
]


def _fake_embed(text: str) -> List[float]:
    vec = [0.0] * settings.EMBEDDING_DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        idx = next((i for i, c in enumerate(_CONCEPTS) if word in c), None)
        slot = idx if idx is not None else 10 + int(hashlib.md5(word.encode()).hexdigest(), 16) % (settings.EMBEDDING_DIM - 10)
        vec[slot] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


_fake: Optional[Callable[[str], List[float]]] = None


def use_fake_embedder(enabled: bool) -> None:
    """Tests only."""
    global _fake
    _fake = _fake_embed if enabled else None


# ─── real model ───────────────────────────────────────────────────────────────

def _load_model():
    import os
    # Windows without Developer Mode cannot create symlinks; the copy fallback works, the warning is noise.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from fastembed import TextEmbedding
    cache = settings.absolute_embedding_cache
    return TextEmbedding(settings.EMBEDDING_MODEL, cache_dir=str(cache))


def provider() -> str:
    if _fake is not None:
        return "fake"
    return "local" if settings.EMBEDDING_PROVIDER.strip().lower() in ("local", "fastembed", "on", "true") else "none"


def status() -> dict:
    p = provider()
    return {
        "provider": p, "model": settings.EMBEDDING_MODEL if p == "local" else ("fake" if p == "fake" else ""),
        "available": p != "none" and (p == "fake" or _model is not None or _failed_at is None),
        "loaded": _model is not None or p == "fake", "last_error": _last_error,
    }


async def _ensure_model():
    """Load the model once (in a thread; the first run downloads it). Returns None if unavailable."""
    global _model, _failed_at, _last_error
    if _model is not None:
        return _model
    if _failed_at is not None and time.monotonic() - _failed_at < RETRY_AFTER_S:
        return None
    async with _lock:
        if _model is not None:
            return _model
        try:
            _model = await asyncio.to_thread(_load_model)
            _failed_at = _last_error = None
        except Exception as exc:  # noqa: BLE001 - offline, missing wheel, disk full ...
            _failed_at = time.monotonic()
            _last_error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
            logger.warning("Embedding model unavailable (%s); using full-text search only.", _last_error)
            return None
    return _model


def _to_list(vec) -> List[float]:
    return [float(x) for x in vec]


async def embed_documents(texts: List[str]) -> Optional[List[List[float]]]:
    """Embeddings for passages, or None if embeddings are off/unavailable."""
    if not texts:
        return []
    if _fake is not None:
        return [_fake(t) for t in texts]
    if provider() == "none":
        return None
    model = await _ensure_model()
    if model is None:
        return None
    try:
        return await asyncio.to_thread(lambda: [_to_list(v) for v in model.embed(texts)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embedding failed: %s", exc.__class__.__name__)
        return None


async def embed_query(text: str) -> Optional[List[float]]:
    if _fake is not None:
        return _fake(text)
    if provider() == "none":
        return None
    model = await _ensure_model()
    if model is None:
        return None
    try:
        return await asyncio.to_thread(lambda: _to_list(next(iter(model.query_embed([text])))))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Query embedding failed: %s", exc.__class__.__name__)
        return None


def vector_literal(vec: List[float]) -> str:
    """pgvector text form. Sent as plain text and cast in SQL (asyncpg has no vector codec)."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
