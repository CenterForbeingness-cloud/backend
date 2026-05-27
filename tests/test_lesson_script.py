"""Tests for deterministic daily lesson coach."""

from app.lesson_script import coach_reply, parse_lesson_beats

DAY1_SNIPPET = """
STEP 1 — Check-in:
Say: "Before we begin, in one short phrase — how are you arriving right now?"
STEP 2 — Settle:
Say: "Thank you. Find a comfortable position."
Then guide breath 1: "Breathe in slowly… and out."
"""


def test_parse_beats():
    beats = parse_lesson_beats(DAY1_SNIPPET)
    assert len(beats) == 3
    assert "Before we begin" in beats[0]


def test_hello_starts_check_in_not_generic():
    beats = parse_lesson_beats(DAY1_SNIPPET)
    reply, idx = coach_reply(beats, 0, "hello")
    assert "Before we begin" in reply
    assert idx == 1


def test_check_in_answer_advances():
    beats = parse_lesson_beats(DAY1_SNIPPET)
    _, idx = coach_reply(beats, 0, "hello")
    reply, idx2 = coach_reply(beats, idx, "tired")
    assert "Thank you" in reply
    assert idx2 == 2


def test_ready_advances_mid_lesson():
    beats = parse_lesson_beats(DAY1_SNIPPET)
    reply, idx = coach_reply(beats, 2, "ready")
    assert "Breathe in" in reply
    assert idx == 3


def test_one_two_does_not_advance():
    beats = parse_lesson_beats(DAY1_SNIPPET)
    reply, idx = coach_reply(beats, 2, "one")
    assert "ready" in reply.lower()
    assert idx == 2
