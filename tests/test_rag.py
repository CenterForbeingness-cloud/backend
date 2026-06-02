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
