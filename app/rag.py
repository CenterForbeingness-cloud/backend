from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol

from app.config import (
    EMBEDDING_MODEL,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    RAG_ENABLED,
    RAG_TOP_K,
    logger,
)

# Path to the always-on grounding script
_BASE_SCRIPT_PATH = Path(__file__).parent.parent.parent / "rag" / "raw" / "base" / "base_script.md"


@dataclass(frozen=True)
class RetrievalHit:
    id: str
    score: float
    course_slug: Optional[str] = None
    source_type: str = "text"
    lesson: Optional[str] = None
    week_number: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "score": self.score,
            "course_slug": self.course_slug,
            "source_type": self.source_type,
            "lesson": self.lesson,
            "week_number": self.week_number,
        }


@dataclass
class RetrievalResult:
    """Contexts for the LLM plus provenance for debugging and analytics."""

    contexts: List[str] = field(default_factory=list)
    retrievals: List[RetrievalHit] = field(default_factory=list)

    @property
    def rag_hit(self) -> bool:
        return len(self.retrievals) > 0

    @property
    def top_score(self) -> Optional[float]:
        if not self.retrievals:
            return None
        return max(hit.score for hit in self.retrievals)


class ContextRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        *,
        course_slug: Optional[str] = None,
        week_number: Optional[int] = None,
    ) -> RetrievalResult: ...


class NoopRetriever:
    def retrieve(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        *,
        course_slug: Optional[str] = None,
        week_number: Optional[int] = None,
    ) -> RetrievalResult:
        return RetrievalResult()


def _hit_from_match(match: dict, *, course_slug: Optional[str]) -> Optional[RetrievalHit]:
    meta = match.get("metadata") or {}
    text = meta.get("text", "")
    if not text:
        return None
    vector_id = str(match.get("id", ""))
    score = float(match.get("score") or 0.0)
    source_type = str(meta.get("source_type") or "text")
    lesson = meta.get("lesson")
    week = meta.get("week_number")
    return RetrievalHit(
        id=vector_id,
        score=score,
        course_slug=meta.get("course_slug") or course_slug,
        source_type=source_type,
        lesson=str(lesson) if lesson is not None else None,
        week_number=int(week) if week is not None else None,
    )


class PineconeRetriever:
    """
    Retrieves relevant content chunks from Pinecone with optional course/week filtering.

    Pinecone metadata filter logic:
      - No course_slug supplied  base namespace only (fallback / general chat)
      - course_slug supplied     courses namespace, filtered by course_slug
      - week_number also supplied additionally filter by week_number
    """

    def __init__(self) -> None:
        from openai import OpenAI
        from pinecone import Pinecone

        self._openai = OpenAI()
        self._index = Pinecone(api_key=PINECONE_API_KEY).Index(PINECONE_INDEX_NAME)
        self._embedding_model = EMBEDDING_MODEL

    def _embed(self, text: str) -> List[float]:
        response = self._openai.embeddings.create(
            model=self._embedding_model,
            input=[text],
        )
        return response.data[0].embedding

    def retrieve(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        *,
        course_slug: Optional[str] = None,
        week_number: Optional[int] = None,
    ) -> RetrievalResult:
        vector = self._embed(query)

        if course_slug:
            meta_filter: dict = {
                "namespace": {"$eq": "courses"},
                "course_slug": {"$eq": course_slug},
            }
            if week_number is not None:
                meta_filter["week_number"] = {"$eq": week_number}
        else:
            meta_filter = {"namespace": {"$eq": "base"}}

        try:
            result = self._index.query(
                vector=vector,
                top_k=top_k,
                filter=meta_filter,
                include_metadata=True,
            )
        except Exception as exc:
            logger.warning("Pinecone query failed: %s", exc)
            return RetrievalResult()

        contexts: List[str] = []
        retrievals: List[RetrievalHit] = []
        for match in result.get("matches", []):
            meta = match.get("metadata") or {}
            text = meta.get("text", "")
            if not text:
                continue
            contexts.append(text)
            hit = _hit_from_match(match, course_slug=course_slug)
            if hit:
                retrievals.append(hit)

        return RetrievalResult(contexts=contexts, retrievals=retrievals)


def load_base_script() -> Optional[str]:
    """Load the always-on grounding script if it exists."""
    if _BASE_SCRIPT_PATH.exists():
        try:
            return _BASE_SCRIPT_PATH.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Could not read base script: %s", exc)
    return None


def build_context_retriever() -> ContextRetriever:
    if not RAG_ENABLED:
        logger.info("RAG disabled; using no-op context retriever")
        return NoopRetriever()

    if not PINECONE_API_KEY or not PINECONE_INDEX_NAME:
        logger.warning(
            "RAG enabled but PINECONE_API_KEY or PINECONE_INDEX_NAME not set; "
            "falling back to no-op retriever"
        )
        return NoopRetriever()

    try:
        retriever = PineconeRetriever()
        logger.info(
            "PineconeRetriever initialised (index=%s, model=%s)",
            PINECONE_INDEX_NAME,
            EMBEDDING_MODEL,
        )
        return retriever
    except Exception as exc:
        logger.error("Failed to initialise PineconeRetriever: %s; using no-op", exc)
        return NoopRetriever()
