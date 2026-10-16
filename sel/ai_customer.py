"""The AI customer: plays the customer in a practice drill, one short line at a time.

It runs on the fast chat client (``SEL_CHAT_LLM_*``). The system prompt holds
the persona and never changes during a drill, and the transcript is appended
to the user message, so a server that caches prompt prefixes reuses its work
between turns. Replies are capped at about 40 tokens and returned as
``{say, end_call, mood}``; code checks them (length, no ids or brackets, not
empty) and falls back to the rules customer when the model fails, so a drill
never stalls.

The rules customer opens with the persona's opening (the real customer line,
anonymised), softens after the rep asks questions about what the customer
needs (how many depends on the difficulty), pushes back otherwise, and ends
the call once a next step is agreed or after a few turns.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .llm import LLMClient, LLMError
from .persona import DIFFICULTY

MOODS = ("calm", "doubtful", "annoyed", "warming")
MAX_SAY = 240
MAX_TURNS = 8  # customer lines before the rules customer says goodbye

CUSTOMER_SYSTEM = """You play a customer on a practice sales call. Stay in character; you are not an assistant.
Who you are: {about}
What you care about: {wants}
Your concern on this call: {opening}
If the salesperson handles it well: {if_good}
If they do not: {if_poor}
Difficulty: {difficulty}
Rules: reply as the customer only, one or two short spoken sentences, under 25 words. Do not invent prices or
product facts; react to what the salesperson says. Set end_call to true only when a clear next step has been
agreed or you are saying goodbye. mood is how you feel now. Return only JSON."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["say", "end_call", "mood"],
          "properties": {"say": {"type": "string", "maxLength": 200}, "end_call": {"type": "boolean"},
                         "mood": {"type": "string", "enum": list(MOODS)}}}
_IDS = re.compile(r"\[[^\]]*\]|\b(?:REP|CUSTOMER)\s*:", re.I)
_STAGE = re.compile(r"\s*[(*]?\b(?:end(?:s|ing)? (?:the )?call|hangs? up)\b[.)*]*\s*$", re.I)


class AICustomer:
    def __init__(self, persona: dict[str, Any], llm: LLMClient | None = None):
        self.persona = persona
        self.llm = llm
        self.difficulty = int(persona.get("difficulty") or 2)
        self.system = CUSTOMER_SYSTEM.format(difficulty=DIFFICULTY.get(self.difficulty, DIFFICULTY[2]),
                                             **{k: persona.get(k, "") for k in ("about", "wants", "opening", "if_good", "if_poor")})
        self.timings: list[float] = []  # seconds per model reply, for scripts/drill_latency.py

    @property
    def name(self) -> str:
        return f"model:{self.llm.name}" if self.llm is not None else "rules (no language model)"

    def opening(self) -> dict[str, Any]:
        return {"say": self.persona.get("opening") or "Hello?", "end_call": False, "mood": "doubtful", "by": "persona"}

    def reply(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """The customer's next line, given the drill so far (``[{role, text}]``, oldest first)."""
        if self.llm is not None:
            t = time.perf_counter()
            try:
                out = self.llm.complete_json(self.system, render(lines), SCHEMA, "customer", max_tokens=80)
                self.timings.append(time.perf_counter() - t)
                # Small models write stage directions into the line ("Sounds good, thanks. End call.").
                say = _STAGE.sub("", " ".join(str(out.get("say") or "").split())).strip()
                if say and not _IDS.search(say):
                    return {"say": say[:MAX_SAY], "end_call": bool(out.get("end_call")) and may_end(lines),
                            "mood": out.get("mood") if out.get("mood") in MOODS else "calm", "by": self.name,
                            "seconds": round(self.timings[-1], 2)}
            except LLMError:
                self.timings.append(time.perf_counter() - t)
        return {**rules_reply(lines, self.difficulty), "by": "rules (no language model)"}


def render(lines: list[dict[str, Any]]) -> str:
    return "\n".join(f"{'SALESPERSON' if x['role'] == 'rep' else 'YOU (CUSTOMER)'}: {x['text']}" for x in lines[-20:]) + \
        "\nYOU (CUSTOMER):"


MIN_TURNS_TO_END = 4  # customer lines before a model may end the call without an agreed next step


def may_end(lines: list[dict[str, Any]]) -> bool:
    """Whether the customer may hang up: a dated next step was just agreed, or the call has gone on a while.

    Small models set end_call on their first reply; code decides, so a drill is never cut short.
    """
    from .dates import due_phrase_in
    from .live import is_commitment, sentences

    reps = [x["text"] for x in lines if x["role"] == "rep"]
    agreed = bool(reps) and any(is_commitment(s, s) and due_phrase_in(s) for s in sentences(reps[-1]))
    return agreed or sum(1 for x in lines if x["role"] == "customer") >= MIN_TURNS_TO_END


def rules_reply(lines: list[dict[str, Any]], difficulty: int = 2) -> dict[str, Any]:
    from .dates import due_phrase_in
    from .live import is_commitment, is_question, is_tag_question, sentences

    rep_lines = [x["text"] for x in lines if x["role"] == "rep"]
    mine = [x for x in lines if x["role"] == "customer"]
    good = sum(1 for text in rep_lines for s in sentences(text) if is_question(s) and not is_tag_question(s))
    last = rep_lines[-1] if rep_lines else ""
    agreed = any(is_commitment(s, s) and due_phrase_in(s) for s in sentences(last))
    if agreed or len(mine) >= MAX_TURNS:
        return {"say": "Okay, that works. Send me the details and we'll talk then. Thank you.", "end_call": True,
                "mood": "warming" if good >= difficulty else "calm"}
    asked = any(is_question(s) and not is_tag_question(s) for s in sentences(last))
    if asked and good >= difficulty and any(x.get("mood") == "warming" for x in mine):
        return {"say": "Okay, I'm listening. What do you suggest we do next?", "end_call": False, "mood": "warming"}
    if asked and good >= difficulty:
        return {"say": "That helps, thank you. So what would the next step be?", "end_call": False, "mood": "warming"}
    if asked:
        return {"say": "Honestly, I just want to be sure what I'm getting for the price before I decide.",
                "end_call": False, "mood": "calm"}
    pushed = sum(1 for x in mine if x.get("mood") in ("doubtful", "annoyed"))
    if pushed >= 2:
        return {"say": "You keep telling me the same thing. I'm still not convinced it's worth it.", "end_call": False,
                "mood": "annoyed"}
    return {"say": "Maybe, but it still seems like a lot to me.", "end_call": False, "mood": "doubtful"}
