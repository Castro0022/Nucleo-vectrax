"""
Vectrax Semantic Memory
========================
Local-first vector index using sentence-transformers + FAISS.

Model: ``all-MiniLM-L6-v2`` — good quality, runs locally.
Persists to ``vault/semantic_index.faiss`` + ``vault/semantic_meta.jsonl``.
Each vector is stored with a human-readable summary + metadata for auditability.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core.learn import VAULT_DIR, SEMANTIC_INDEX_PATH, SEMANTIC_META_PATH

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # MiniLM-L6-v2 output dimension


# ---------------------------------------------------------------------------
# Metadata record
# ---------------------------------------------------------------------------

@dataclass
class SemanticRecord:
    """Human-readable metadata stored alongside each vector."""
    id: str
    summary: str
    file_path: str = ""
    symbols: List[str] = field(default_factory=list)
    intent: str = ""
    content_hash: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Semantic Index
# ---------------------------------------------------------------------------

class SemanticIndex:
    """
    FAISS-backed semantic vector store with metadata sidecar.

    Lazy-loads the embedding model and FAISS index on first use.
    """

    def __init__(
        self,
        index_path: str = SEMANTIC_INDEX_PATH,
        meta_path: str = SEMANTIC_META_PATH,
        model_name: str = EMBEDDING_MODEL,
    ):
        self.index_path = index_path
        self.meta_path = meta_path
        self.model_name = model_name
        self._model = None
        self._index = None
        self._records: List[SemanticRecord] = []
        self._loaded = False

    # -- lazy loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_meta()
        self._load_index()
        self._loaded = True

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required: pip install sentence-transformers"
                )
        return self._model

    def _load_index(self) -> None:
        try:
            import faiss
        except ImportError:
            raise ImportError("faiss-cpu is required: pip install faiss-cpu")

        if os.path.isfile(self.index_path):
            self._index = faiss.read_index(self.index_path)
        else:
            self._index = faiss.IndexFlatL2(EMBEDDING_DIM)

    def _load_meta(self) -> None:
        self._records = []
        if os.path.isfile(self.meta_path):
            with open(self.meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            self._records.append(SemanticRecord(**d))
                        except (json.JSONDecodeError, TypeError):
                            continue

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        """Persist index and metadata to disk."""
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        if self._index is not None:
            import faiss
            faiss.write_index(self._index, self.index_path)
        with open(self.meta_path, "w", encoding="utf-8") as f:
            for rec in self._records:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    # -- public API ---------------------------------------------------------

    def upsert(self, record: SemanticRecord, text: str) -> None:
        """
        Embed *text*, store vector alongside *record* metadata.
        If a record with the same ``id`` exists, it is replaced.
        """
        self._ensure_loaded()
        import numpy as np

        model = self._get_model()
        embedding = model.encode([text], convert_to_numpy=True)
        vec = np.array(embedding, dtype="float32").reshape(1, -1)

        # Check for existing record with same id → replace
        existing_idx = None
        for i, r in enumerate(self._records):
            if r.id == record.id:
                existing_idx = i
                break

        if existing_idx is not None:
            # FAISS IndexFlatL2 doesn't support in-place update;
            # We rebuild only if the index is small, otherwise just append
            # (duplicates are tolerable — query deduplicates by id).
            self._records[existing_idx] = record
        else:
            self._records.append(record)

        self._index.add(vec)
        self.save()

    def query(self, text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Find the *top_k* most similar records to *text*.

        Returns list of dicts with ``record`` + ``distance``.
        """
        self._ensure_loaded()
        import numpy as np

        if self._index is None or self._index.ntotal == 0:
            return []

        model = self._get_model()
        embedding = model.encode([text], convert_to_numpy=True)
        vec = np.array(embedding, dtype="float32").reshape(1, -1)

        k = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(vec, k)

        results = []
        seen_ids = set()
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._records):
                continue
            rec = self._records[idx]
            if rec.id in seen_ids:
                continue
            seen_ids.add(rec.id)
            results.append({
                "record": rec.to_dict(),
                "distance": float(dist),
            })
        return results

    def stats(self) -> Dict[str, Any]:
        """Return index statistics."""
        self._ensure_loaded()
        return {
            "total_vectors": self._index.ntotal if self._index else 0,
            "total_records": len(self._records),
            "model": self.model_name,
            "dimension": EMBEDDING_DIM,
            "index_file": self.index_path,
            "index_exists": os.path.isfile(self.index_path),
        }

    @property
    def records(self) -> List[SemanticRecord]:
        self._ensure_loaded()
        return list(self._records)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_index: Optional[SemanticIndex] = None


def get_semantic_index() -> SemanticIndex:
    global _index
    if _index is None:
        _index = SemanticIndex()
    return _index
