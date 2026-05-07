from typing import List, Protocol
from app.config import RAG_ENABLED, logger

class ContextRetriever(Protocol):
    def retrieve(self, query: str, top_k: int = 3) -> List[str]: ...


class NoopRetriever:
    def retrieve(self, query: str, top_k: int = 3) -> List[str]:
        return []


def build_context_retriever() -> ContextRetriever:
    if not RAG_ENABLED:
        logger.info("RAG disabled; using no-op context retriever")
        return NoopRetriever()

    # Placeholder seam for future Pinecone/Weaviate integration.
    logger.info("RAG enabled but retriever not implemented yet; using no-op retriever")
    return NoopRetriever()

