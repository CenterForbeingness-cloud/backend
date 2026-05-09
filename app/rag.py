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


class ContextRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        *,
        course_slug: Optional[str] = None,
        week_number: Optional[int] = None,
    ) -> List[str]: ...


class NoopRetriever:
    def retrieve(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        *,
        course_slug: Optional[str] = None,
        week_number: Optional[int] = None,
    ) -> List[str]:
        return []


class PineconeRetriever:
    """
    Retrieves relevant content chunks from Pinecone with optional course/week filtering.

    Pinecone metadata filter logic:
      - No course_slug supplied  → base namespace only (fallback / general chat)
      - course_slug supplied     → courses namespace, filtered by course_slug
      - week_number also supplied → additionally filter by week_number
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
    ) -> List[str]:
        vector = self._embed(query)

        # Build metadata filter
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
            return []

        chunks: List[str] = []
        for match in result.get("matches", []):
            text = (match.get("metadata") or {}).get("text", "")
            if text:
                chunks.append(text)
        return chunks


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
        logger.info("PineconeRetriever initialised (index=%s, model=%s)", PINECONE_INDEX_NAME, EMBEDDING_MODEL)
        return retriever
    except Exception as exc:
        logger.error("Failed to initialise PineconeRetriever: %s; using no-op", exc)
        return NoopRetriever()

