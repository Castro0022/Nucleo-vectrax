"""
Embedding generation and similarity search.

Primary:  sentence-transformers (all-MiniLM-L6-v2)
Index:    FAISS if available, else brute-force numpy cosine similarity
Encoding: numpy float32 arrays serialised to bytes for SQLite BLOB storage
"""
from __future__ import annotations

import io
from typing import List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Model singleton
# ---------------------------------------------------------------------------
_model = None


def _get_model():
    global _model
    if _model is None:
        import logging as _logging
        import warnings
        # Suppress "unauthenticated requests to HF Hub" warning.
        # The model is already cached locally in the Docker image; no auth needed.
        # HF Hub emits via both warnings.warn() and logging.warning().
        _hf_logger = _logging.getLogger("huggingface_hub.utils._http")
        _prev_level = _hf_logger.level
        _hf_logger.setLevel(_logging.ERROR)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=".*unauthenticated.*HF Hub.*"
            )
            from sentence_transformers import SentenceTransformer  # lazy import
            _model = SentenceTransformer("all-MiniLM-L6-v2")
        _hf_logger.setLevel(_prev_level)
    return _model


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def encode_embedding(vec: np.ndarray) -> bytes:
    """Serialise a float32 numpy vector to bytes for SQLite storage."""
    buf = io.BytesIO()
    np.save(buf, vec.astype(np.float32))
    return buf.getvalue()


def decode_embedding(data: bytes) -> np.ndarray:
    """Deserialise bytes back to a float32 numpy vector."""
    buf = io.BytesIO(data)
    return np.load(buf)


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------

def embed(text: str) -> np.ndarray:
    """Return a normalised float32 embedding vector for the given text."""
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return vec.astype(np.float32)


def get_embedder():
    """Return the SentenceTransformer model singleton.

    Used by StrategicRouter and SemanticClassifier which need the
    raw model for batch .encode() calls.
    """
    return _get_model()


# ---------------------------------------------------------------------------
# Cosine similarity (numpy brute-force)
# ---------------------------------------------------------------------------

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine similarity between two unit-normalised vectors."""
    # Already normalised by sentence-transformers, so dot product == cosine sim
    return float(np.dot(a, b))


def find_similar(
    query_vec: np.ndarray,
    star_embeddings: List[Tuple[str, np.ndarray]],
    threshold: float = 0.75,
) -> List[Tuple[str, float]]:
    """
    Given a query embedding and a list of (star_id, embedding) pairs,
    return all pairs whose cosine similarity to the query exceeds `threshold`,
    sorted by similarity descending.

    Falls back to FAISS if available and the corpus is large (> 1000 stars).
    """
    if not star_embeddings:
        return []

    ids = [sid for sid, _ in star_embeddings]
    matrix = np.stack([v for _, v in star_embeddings])  # (N, D)

    # Try FAISS for large corpora
    if len(ids) > 1000:
        try:
            return _faiss_search(query_vec, ids, matrix, threshold)
        except ImportError:
            pass  # fall through to numpy

    # Brute-force cosine similarity
    sims = matrix @ query_vec  # dot product of unit vectors = cosine sim
    results = [
        (ids[i], float(sims[i]))
        for i in range(len(ids))
        if float(sims[i]) >= threshold
    ]
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _faiss_search(
    query_vec: np.ndarray,
    ids: List[str],
    matrix: np.ndarray,
    threshold: float,
) -> List[Tuple[str, float]]:
    import faiss  # noqa: F401

    dim = matrix.shape[1]
    index = faiss.IndexFlatIP(dim)   # Inner Product == cosine sim for unit vecs
    index.add(matrix.astype(np.float32))

    k = min(len(ids), 100)
    D, I = index.search(query_vec.reshape(1, -1).astype(np.float32), k)

    results = []
    for score, idx in zip(D[0], I[0]):
        if idx >= 0 and float(score) >= threshold:
            results.append((ids[idx], float(score)))
    return results


# ---------------------------------------------------------------------------
# Average embedding for a set of vectors (constellation centroid)
# ---------------------------------------------------------------------------

def mean_embedding(vecs: List[np.ndarray]) -> np.ndarray:
    """Return the mean (centroid) of a list of unit-normalised embeddings."""
    mat = np.stack(vecs)
    mean = mat.mean(axis=0)
    norm = np.linalg.norm(mean)
    if norm > 0:
        mean /= norm
    return mean.astype(np.float32)


def cluster_coherence(vecs: List[np.ndarray]) -> float:
    """
    Measure how coherent a cluster is: average pairwise cosine similarity
    between all vectors. Returns a value in [0, 1].
    """
    if len(vecs) < 2:
        return 1.0
    mat = np.stack(vecs)
    gram = mat @ mat.T  # (N, N) pairwise cosine similarities
    n = len(vecs)
    # Sum of off-diagonal elements divided by number of pairs
    total = (gram.sum() - np.trace(gram)) / (n * (n - 1))
    return float(np.clip(total, 0.0, 1.0))
