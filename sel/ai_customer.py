"""The AI customer: plays the customer in a practice drill, one short line at a time.

It runs on the fast chat client (``SEL_CHAT_LLM_*``) in one of two formats
(``SEL_CUSTOMER_FORMAT``):

* ``text`` (default): the drill is sent as chat turns (the rep as the user,
  the customer as the assistant) and the model writes only the spoken line.
  Each request's prompt is the previous one plus two turns, so a local server
  that caches prompt prefixes (llama.cpp, Ollama) reads only the new words;
  the reply is a sentence, not a JSON object, so there is no grammar to
  follow and fewer tokens to write. Code decides when the call ends (a dated
  next step agreed, or enough turns) and the mood (from Sel's rules), which
  small models got wrong anyway.
* ``json``: one user message holding the transcript, a ``{say, end_call,
  mood}`` reply under a JSON schema. Kept to compare (scripts/drill_latency.py
  ``--format``) and for clients without plain-text chat.

Both are checked by code (length, no ids or brackets, not empty) and fall back
to the rules customer when the model fails, so a drill never stalls. A
warm-up request when the room opens loads the model and reads the persona
before the rep's first line.

The rules customer opens with the persona's opening (the real customer line,
anonymised), softens after the rep asks questions about what the customer
needs (how many depends on the difficulty), pushes back otherwise, and ends
the call once a next step is agreed or after a few turns.
"""

from __future__ import annotations

import os
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
# The plain-text format: the same persona, and the line itself as the whole reply.
TEXT_SYSTEM = """You play a customer on a practice sales call. Stay in character; you are not an assistant.
Who you are: {about}
What you care about: {wants}
Your concern on this call: {opening}
If the salesperson handles it well: {if_good}
If they do not: {if_poor}
Difficulty: {difficulty}
Reply with only what you say out loud: one or two short sentences, under 25 words. No names, labels, quotes or
stage directions. Do not invent prices or product facts; react to what the salesperson says."""
CONNECTS = "(The salesperson calls you. You answer.)"  # a constant first turn, so every prompt shares one prefix
TEXT_MAX_TOKENS = 48
FORMATS = ("text", "json")
_LABEL = re.compile(r"^\s*(?:you\s*\(customer\)|customer|me|you|[A-Z][a-z]+)\s*:\s*", re.I)
_ASIDE = re.compile(r"(?:\s*(?:\([^()]{1,40}\)|\*[^*]{1,40}\*))+\s*$")  # trailing "(pauses)", "*sighs*"
_ASIDE_LEAD = re.compile(r"^(?:\s*(?:\([^()]{1,40}\)|\*[^*]{1,40}\*))+\s*")
_IDS = re.compile(r"\[[^\]]*\]|\b(?:REP|CUSTOMER)\s*:", re.I)
_STAGE = re.compile(r"\s*[(*]?\b(?:end(?:s|ing)? (?:the )?call|hangs? up)\b[.)*]*\s*$", re.I)


class AICustomer:
    def __init__(self, persona: dict[str, Any], llm: LLMClient | None = None, fmt: str | None = None):
        self.persona = persona
        self.llm = llm
        self.difficulty = int(persona.get("difficulty") or 2)
        fields = {k: persona.get(k, "") for k in ("about", "wants", "opening", "if_good", "if_poor")}
        level = DIFFICULTY.get(self.difficulty, DIFFICULTY[2])
        self.system = CUSTOMER_SYSTEM.format(difficulty=level, **fields)
        self.text_system = TEXT_SYSTEM.format(difficulty=level, **fields)
        fmt = (fmt or os.environ.get("SEL_CUSTOMER_FORMAT") or "text").lower()
        if fmt not in FORMATS:
            fmt = "text"
        # A client without plain-text chat (scripted test models) uses the JSON format.
        self.format = fmt if fmt == "json" or hasattr(llm, "complete_chat") else "json"
        self.timings: list[float] = []  # seconds per model reply, for scripts/drill_latency.py

    @property
    def name(self) -> str:
        return f"model:{self.llm.name}" if self.llm is not None else "rules (no language model)"

    def opening(self) -> dict[str, Any]:
        return {"say": self.persona.get("opening") or "Hello?", "end_call": False, "mood": "doubtful", "by": "persona"}

    def warm(self) -> None:
        """Load the model and read the persona before the rep speaks (one token, result ignored)."""
        if self.llm is None:
            return
        try:
            if self.format == "text":
                self.llm.complete_chat(turns(self.text_system, [{"role": "customer", "text": self.opening()["say"]}],
                                             open_turn=False), max_tokens=1)
            else:
                self.llm.complete_json(self.system, render([]), SCHEMA, "customer-warm-up", max_tokens=1)
        except LLMError:
            pass  # a cold first reply is slower, nothing more

    def reply(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """The customer's next line, given the drill so far (``[{role, text}]``, oldest first)."""
        if self.llm is not None and self.format == "text":
            return self._reply_text(lines)
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

    def _reply_text(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        rules = rules_reply(lines, self.difficulty)
        if rules["end_call"]:
            # A dated next step was agreed, or the call has run its course: code ends it, as before.
            return {**rules, "by": "rules (no language model)"} if sum(
                1 for x in lines if x["role"] == "customer") >= MAX_TURNS else self._model_goodbye(lines, rules)
        t = time.perf_counter()
        try:
            out = self.llm.complete_chat(turns(self.text_system, lines), max_tokens=TEXT_MAX_TOKENS, stop=["\n"])
            self.timings.append(time.perf_counter() - t)
        except LLMError:
            self.timings.append(time.perf_counter() - t)
            return {**rules, "by": "rules (no language model)"}
        say = clean_line(out)
        if not say:
            return {**rules, "by": "rules (no language model)"}
        # Mood comes from Sel's rules (how the rep has done so far), not from the model.
        return {"say": say, "end_call": False, "mood": rules["mood"], "by": self.name,
                "seconds": round(self.timings[-1], 2)}

    def _model_goodbye(self, lines: list[dict[str, Any]], rules: dict[str, Any]) -> dict[str, Any]:
        """The model's own words for agreeing to the next step; the rules decided the call ends."""
        t = time.perf_counter()
        try:
            out = self.llm.complete_chat(turns(self.text_system, lines), max_tokens=TEXT_MAX_TOKENS, stop=["\n"])
            self.timings.append(time.perf_counter() - t)
            say = clean_line(out)
        except LLMError:
            self.timings.append(time.perf_counter() - t)
            say = ""
        if not say:
            return {**rules, "by": "rules (no language model)"}
        return {"say": say, "end_call": True, "mood": rules["mood"], "by": self.name,
                "seconds": round(self.timings[-1], 2)}


def turns(system: str, lines: list[dict[str, Any]], open_turn: bool = True) -> list[dict[str, str]]:
    """The drill as chat turns: the rep is the user, the customer the assistant.

    Starts with the same two messages every time (the persona and a constant
    "the call connects" turn), and consecutive lines by one side are joined, so
    each request is the previous one with the newest turns appended.
    """
    msgs: list[dict[str, str]] = [{"role": "system", "content": system}, {"role": "user", "content": CONNECTS}]
    for x in lines:
        role = "user" if x["role"] == "rep" else "assistant"
        text = " ".join(str(x.get("text") or "").split())
        if not text:
            continue
        if msgs[-1]["role"] == role:
            msgs[-1] = {"role": role, "content": msgs[-1]["content"] + " " + text}
        else:
            msgs.append({"role": role, "content": text})
    if open_turn and msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "content": "(silence)"})
    return msgs


def clean_line(text: str) -> str:
    """What the customer says: one line, no speaker label, quotes or stage directions; empty if unusable."""
    line = " ".join(str(text or "").split())
    line = _LABEL.sub("", line).strip()
    line = _ASIDE.sub("", _ASIDE_LEAD.sub("", line)).strip()
    line = _STAGE.sub("", line).strip().strip('"\u201c\u201d\'').strip()
    if not line or _IDS.search(line):
        return ""
    # Keep at most two sentences, as the prompt asks.
    parts = re.split(r"(?<=[.!?])\s+", line)
    return " ".join(parts[:2])[:MAX_SAY]


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
