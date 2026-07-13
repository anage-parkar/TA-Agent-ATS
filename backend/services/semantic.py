"""Semantic skill/experience overlap (Workstream D).

The target design is pgvector-backed cosine similarity over embeddings of the
job requirements and candidate profile (`candidates.embedding vector(1536)`
already exists). That needs an embedding model, which the current Max-plan CLI
setup does not provide — so the ACTIVE implementation is a lexical overlap
(normalized token Jaccard), and the embedding path is a documented seam:
set an embedder via `set_embedder(fn)` and `semantic_skill_overlap` will use
cosine similarity instead.

The overlap is a 0..1 hint passed to the LLM scorer for fuzzy matching; it does
not replace the LLM's per-dimension judgement.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# Pluggable embedder: text -> vector. None = no embeddings available (lexical).
_embedder: Optional[Callable[[list[str]], list[list[float]]]] = None


def set_embedder(fn: Callable[[list[str]], list[list[float]]] | None) -> None:
    """Register an embedding function to enable the pgvector/cosine path."""
    global _embedder
    _embedder = fn


def _tokens(items: list[str]) -> set[str]:
    out: set[str] = set()
    for s in items or []:
        for tok in re.split(r"[^a-z0-9+#.]+", str(s).lower()):
            if len(tok) >= 2:
                out.add(tok)
    return out


def lexical_overlap(a: list[str], b: list[str]) -> float:
    """Normalized token Jaccard overlap in [0, 1]."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cosine(u: list[float], v: list[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu = sum(x * x for x in u) ** 0.5
    nv = sum(y * y for y in v) ** 0.5
    return dot / (nu * nv) if nu and nv else 0.0


def semantic_skill_overlap(job_terms: list[str], candidate_terms: list[str]) -> float:
    """Fuzzy overlap of job vs candidate skills/experience, 0..1.

    Uses embeddings (cosine) when an embedder is registered, else lexical.
    """
    if _embedder is not None and job_terms and candidate_terms:
        try:
            jv, cv = _embedder([" ".join(job_terms)]), _embedder([" ".join(candidate_terms)])
            return max(0.0, min(1.0, _cosine(jv[0], cv[0])))
        except Exception:  # noqa: BLE001 — fall back to lexical on any embedder failure
            pass
    return lexical_overlap(job_terms, candidate_terms)
