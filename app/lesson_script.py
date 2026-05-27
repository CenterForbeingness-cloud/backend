"""
Parse daily schedule copy into ordered beats and advance deterministically.

Daily lessons do not rely on the LLM to track steps — that caused generic "hello"
replies and rushing through the script.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Tuple

# Lines like: Say: "..." / Then: "..." / Then guide breath 1: "..." / Pause cue: "..."
_BEAT_PATTERN = re.compile(
    r'(?:Say|Then(?:\s+guide\s+breath\s+\d+)?|Pause\s+cue):\s*"([^"]+)"',
    re.IGNORECASE,
)

GREETING_RE = re.compile(
    r"^(hi|hello|hey|howdy|good\s+(morning|afternoon|evening))[\s,!.?]*$",
    re.IGNORECASE,
)
GREETING_PREFIX_RE = re.compile(r"^(hi|hello|hey)\b", re.IGNORECASE)


def is_greeting(message: str) -> bool:
    lower = message.strip().lower()
    if not lower:
        return False
    return bool(GREETING_RE.match(lower) or GREETING_PREFIX_RE.match(lower))

ADVANCE_RE = re.compile(
    r"^(ready|yes|yep|yeah|y|ok|okay|sure|next|continue|go\s*on|"
    r"let'?s\s*go|begin|start|done|finished|stop)[\s!.?]*$",
    re.IGNORECASE,
)


def parse_lesson_beats(content: str) -> List[str]:
    """Extract spoken lines from schedule step blocks, in file order."""
    beats = [m.group(1).strip() for m in _BEAT_PATTERN.finditer(content)]
    return [b for b in beats if b]


@lru_cache(maxsize=64)
def cached_lesson_beats(content: str) -> Tuple[str, ...]:
    return tuple(parse_lesson_beats(content))


def clear_lesson_beats_cache() -> None:
    cached_lesson_beats.cache_clear()


def coach_reply(beats: List[str], beat_index: int, user_message: str) -> Tuple[str, int]:
    """
    Return (assistant_reply, new_beat_index).

    beat_index is the next beat to deliver on advance (0 = start of lesson).
    """
    if not beats:
        return (
            "Today's lesson script is empty. Please contact support or try again later.",
            0,
        )

    msg = user_message.strip()
    lower = msg.lower()

    if beat_index >= len(beats):
        return (
            "You've finished today's practice. Tap **Complete day** in the app when you're ready.",
            beat_index,
        )

    # Step 1 check-in: greeting → first beat, stay waiting for their answer
    if beat_index == 0:
        if is_greeting(msg) or not msg:
            return beats[0], 1
        # First message without hello still starts check-in
        return beats[0], 1

    # Awaiting check-in answer (one short phrase) — any reply advances to settle/breaths
    if beat_index == 1:
        if is_greeting(msg):
            return beats[0], 1
        return beats[1], 2

    # Mid-lesson: only explicit advance triggers move forward (no rushing on "one"/"two" alone
    # unless they match ADVANCE_RE — user must say ready/next between beats)
    if ADVANCE_RE.match(lower):
        reply = beats[beat_index]
        return reply, beat_index + 1

    if is_greeting(msg):
        return (
            "Let's stay with today's practice. Say **ready** when you want the next part.",
            beat_index,
        )

    # Off-topic or chatter — do not advance
    return (
        "Say **ready** when you want the next part of today's practice.",
        beat_index,
    )
