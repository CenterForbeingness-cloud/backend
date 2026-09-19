from app.rag import NoopRetriever, RetrievalHit, RetrievalResult, _hit_from_match


def test_noop_retriever_empty_provenance():
    result = NoopRetriever().retrieve("hello", course_slug="week-zero-reset")
    assert result.contexts == []
    assert result.retrievals == []
    assert result.rag_hit is False
    assert result.top_score is None


def test_hit_from_match_audio_metadata():
    match = {
        "id": "vec-1",
        "score": 0.87,
        "metadata": {
            "text": "Breathe in for four counts.",
            "source_type": "audio",
            "lesson": "lesson-01",
            "course_slug": "week-zero-reset",
        },
    }
    hit = _hit_from_match(match, course_slug="week-zero-reset")
    assert hit is not None
    d = hit.to_dict()
    assert d["id"] == "vec-1"
    assert d["score"] == 0.87
    assert d["source_type"] == "audio"
    assert d["lesson"] == "lesson-01"


def test_retrieval_result_top_score():
    hits = [
        RetrievalHit(id="a", score=0.5, source_type="text"),
        RetrievalHit(id="b", score=0.9, source_type="audio", lesson="lesson-02"),
    ]
    result = RetrievalResult(contexts=["chunk"], retrievals=hits)
    assert result.rag_hit is True
    assert result.top_score == 0.9


def test_pinecone_retriever_companion_queries_ben_namespace():
    from app.rag import PineconeRetriever

    retriever = PineconeRetriever.__new__(PineconeRetriever)
    retriever._embedding_model = "text-embedding-3-small"
    retriever._embed = lambda text: [0.1, 0.2]  # type: ignore[method-assign]
    captured: dict = {}

    class FakeIndex:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {
                "matches": [
                    {
                        "id": "ben-1",
                        "score": 0.91,
                        "metadata": {
                            "text": "Awareness is already here.",
                            "namespace": "ben",
                            "source_type": "text",
                            "lesson": "awareness",
                        },
                    }
                ]
            }

    retriever._index = FakeIndex()  # type: ignore[attr-defined]
    result = retriever.retrieve("What is awareness?")
    assert captured["filter"] == {"namespace": {"$eq": "ben"}}
    assert result.rag_hit is True
    assert result.contexts == ["Awareness is already here."]


def test_pinecone_retriever_course_queries_courses_namespace():
    from app.rag import PineconeRetriever

    retriever = PineconeRetriever.__new__(PineconeRetriever)
    retriever._embedding_model = "text-embedding-3-small"
    retriever._embed = lambda text: [0.1, 0.2]  # type: ignore[method-assign]
    captured: dict = {}

    class FakeIndex:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {"matches": []}

    retriever._index = FakeIndex()  # type: ignore[attr-defined]
    result = retriever.retrieve(
        "What is the witness?",
        course_slug="week-zero-reset",
        week_number=1,
    )
    assert captured["filter"] == {
        "namespace": {"$eq": "courses"},
        "course_slug": {"$eq": "week-zero-reset"},
        "week_number": {"$eq": 1},
    }
    assert result.rag_hit is False


def test_pinecone_retriever_empty_ben_index_fails_soft():
    from app.rag import PineconeRetriever

    retriever = PineconeRetriever.__new__(PineconeRetriever)
    retriever._embedding_model = "text-embedding-3-small"
    retriever._embed = lambda text: [0.1, 0.2]  # type: ignore[method-assign]

    class FakeIndex:
        def query(self, **kwargs):
            return {"matches": []}

    retriever._index = FakeIndex()  # type: ignore[attr-defined]
    result = retriever.retrieve("Hello")
    assert result.contexts == []
    assert result.retrievals == []
    assert result.rag_hit is False
