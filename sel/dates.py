"""Turn a spoken due phrase into a date, or say why it can't be done.

The language model's job is to copy the due phrase verbatim from the call
("bholi beluka samma", "sometime next week"). Resolving it to a calendar date
is arithmetic, so it happens here, deterministically, relative to the time the
call started. When the words don't pin down a single day, the result is
``ambiguous`` with a reason; Sel then asks the user instead of guessing.

Covers English and romanized/Devanagari Nepali for the common cases. Absolute
dates (including Bikram Sambat dates) are not handled yet and resolve as
``ambiguous`` so a person checks them.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .models import DueResolution
from .pack import WEEKDAYS

# Each entry maps a spoken word to a weekday index (Monday == 0).
_WEEKDAY_WORDS: dict[str, int] = {
    **{name: i for i, name in enumerate(WEEKDAYS)},
    # Three-letter abbreviations, except "sat" and "sun", which are ordinary words.
    **{name[:3]: i for i, name in enumerate(WEEKDAYS) if name[:3] not in ("sat", "sun")},
    "sombar": 0, "mangalbar": 1, "budhabar": 2, "bihibar": 3,
    "sukrabar": 4, "shukrabar": 4, "sanibar": 5, "shanibar": 5, "aaitabar": 6, "aitabar": 6,
    "सोमबार": 0, "मंगलबार": 1, "बुधबार": 2, "बिहीबार": 3, "शुक्रबार": 4, "शनिबार": 5, "आइतबार": 6,
}

_RELATIVE_DAYS: list[tuple[tuple[str, ...], int]] = [
    # "day after tomorrow" is matched separately so that "tomorrow" inside it doesn't count.
    (("parsi", "पर्सि", "पर्सी"), 2),
    (("tomorrow", "bholi", "voli", "भोलि"), 1),
    (("today", "aaja", "aaj", "aja", "आज"), 0),
]

_PART_OF_DAY: dict[str, str] = {
    "morning": "morning", "bihana": "morning", "बिहान": "morning",
    "afternoon": "afternoon", "diuso": "afternoon", "दिउँसो": "afternoon",
    "evening": "evening", "beluka": "evening", "saanjh": "evening", "बेलुका": "evening",
    "night": "night", "raati": "night", "rati": "night", "राति": "night",
}

# Phrases that, on their own, never name a single day.
_VAGUE = (
    "sometime", "some time", "soon", "later", "asap", "when possible", "few days",
    "kunai bela", "kunai din", "chhito", "chito", "pachhi", "paxi", "kehi din",
)
_NEXT_WEEK = ("next week", "arko hapta", "aarko hapta", "agla hapta", "अर्को हप्ता")
_THIS_WEEK = ("this week", "yo hapta", "यो हप्ता")

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ek": 1, "dui": 2, "tin": 3, "char": 4, "paanch": 5}

_CLOCK_EN = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", re.I)
_CLOCK_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_CLOCK_NE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:baje|bajey|bajechi|बजे)")
_IN_DAYS = re.compile(r"\b(?:in|within)\s+(\d+|" + "|".join(_NUM_WORDS) + r")\s+days?\b|\b(\d+|"
                      + "|".join(_NUM_WORDS) + r")\s+din\s+(?:ma|bhitra)\b")


def _has(text: str, words: tuple[str, ...] | list[str]) -> str | None:
    for w in words:
        if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text):
            return w
    return None


def _week_bounds(day: date, week_starts_on: str) -> tuple[date, date]:
    start_idx = WEEKDAYS.index(week_starts_on)
    offset = (day.weekday() - start_idx) % 7
    start = day - timedelta(days=offset)
    return start, start + timedelta(days=6)


def _resolve_time(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (HH:MM or None, part_of_day or None, reason the time is unclear or None)."""
    part = next((v for k, v in _PART_OF_DAY.items() if _has(text, (k,))), None)

    m = _CLOCK_EN.search(text)
    if m:
        hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).lower().startswith("p"):
            hour += 12
        return f"{hour:02d}:{minute:02d}", part, None

    m = _CLOCK_24.search(text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}", part, None

    m = _CLOCK_NE.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if hour > 12:
            return f"{hour:02d}:{minute:02d}", part, None
        if part in ("afternoon", "evening", "night") and hour < 12:
            hour += 12
        elif part is None:
            # "3 baje" alone does not say morning or afternoon.
            return None, None, f"'{m.group(0)}' does not say morning or afternoon"
        return f"{hour % 24:02d}:{minute:02d}", part, None

    return None, part, None


def resolve_due(phrase: str | None, call_started_at: str, week_starts_on: str = "monday") -> DueResolution:
    if not phrase or not phrase.strip():
        return DueResolution(status="missing", reason="no time was mentioned for this commitment")

    text = phrase.strip().lower()
    call_day = datetime.fromisoformat(call_started_at).date()
    time, part, time_problem = _resolve_time(text)

    def exact(day: date) -> DueResolution:
        return DueResolution(
            status="exact", date=day.isoformat(), time=time, part_of_day=part, reason=time_problem
        )

    def ambiguous(reason: str, window: tuple[date, date] | None = None) -> DueResolution:
        return DueResolution(
            status="ambiguous",
            part_of_day=part,
            window_start=window[0].isoformat() if window else None,
            window_end=window[1].isoformat() if window else None,
            reason=reason,
        )

    if _has(text, _NEXT_WEEK):
        this_start, _ = _week_bounds(call_day, week_starts_on)
        nxt = this_start + timedelta(days=7)
        weekday = _first_weekday(text)
        if weekday is not None:
            day = nxt + timedelta(days=(weekday - nxt.weekday()) % 7)
            return exact(day)
        return ambiguous("'next week' does not name a day", (nxt, nxt + timedelta(days=6)))

    vague = _has(text, _VAGUE)
    if vague:
        return ambiguous(f"'{vague}' does not name a day")

    rest = text.replace("day after tomorrow", " ")
    deltas = {delta for words, delta in _RELATIVE_DAYS if _has(rest, words)}
    if rest != text:
        deltas.add(2)
    if len(deltas) > 1:
        days = sorted(call_day + timedelta(days=d) for d in deltas)
        return ambiguous("more than one day was mentioned", (days[0], days[-1]))
    if deltas:
        return exact(call_day + timedelta(days=deltas.pop()))

    m = _IN_DAYS.search(text)
    if m:
        raw = m.group(1) or m.group(2)
        n = int(raw) if raw.isdigit() else _NUM_WORDS[raw]
        return exact(call_day + timedelta(days=n))

    named = {i for w, i in _WEEKDAY_WORDS.items() if _has(text, (w,))}
    if len(named) > 1:
        return ambiguous("more than one weekday was mentioned")
    weekday = _first_weekday(text)
    if weekday is not None:
        word = _has(text, sorted(_WEEKDAY_WORDS, key=len, reverse=True))
        if re.search(r"(?<!\w)(?:next|arko|aarko)\s+" + re.escape(word or ""), text):
            # "next Friday" said on a Wednesday can mean in two days or in nine.
            soonest = call_day + timedelta(days=(weekday - call_day.weekday()) % 7 or 7)
            return ambiguous(
                f"'next {word}' can mean {soonest.isoformat()} or the week after",
                (soonest, soonest + timedelta(days=7)),
            )
        if weekday == call_day.weekday():
            return ambiguous(
                f"'{word}' is the same weekday as the call; today or next week?",
                (call_day, call_day + timedelta(days=7)),
            )
        return exact(call_day + timedelta(days=(weekday - call_day.weekday()) % 7))

    if _has(text, _THIS_WEEK):
        _, end = _week_bounds(call_day, week_starts_on)
        return ambiguous("'this week' does not name a day", (call_day, end))

    if time and not time_problem:
        # A clock time with no day word most often means today, but that's a guess.
        return ambiguous(f"a time ({time}) was given without a day")

    return ambiguous(f"could not interpret {phrase!r} as a day")


def _first_weekday(text: str) -> int | None:
    word = _has(text, sorted(_WEEKDAY_WORDS, key=len, reverse=True))
    return _WEEKDAY_WORDS[word] if word else None


# Words that can be part of a spoken due phrase; used to widen a phrase a model copied too narrowly
# ("beluka samma" when the line says "bholi beluka samma").
TIME_WORDS: frozenset[str] = frozenset(
    {w for words, _ in _RELATIVE_DAYS for w in words}
    | set(_WEEKDAY_WORDS) | set(_PART_OF_DAY)
    | {"day", "after", "next", "this", "coming", "by", "on", "at", "before", "samma", "bhitra", "ma", "nai",
       "arko", "aarko", "yo", "hapta", "week", "baje", "am", "pm", "a.m.", "p.m.", "din", "days", "in"}
)
_TIMEISH = re.compile(r"^\d{1,2}(?::\d{2})?(?:am|pm)?$")


def is_time_word(word: str) -> bool:
    w = word.strip(".,;:!?\"'()").lower()
    return bool(w) and (w in TIME_WORDS or bool(_TIMEISH.match(w)))
