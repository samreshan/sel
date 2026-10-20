"""Practice personas: an anonymised customer built from one real weak moment.

The AI customer in a drill plays the customer from a real call, so the rep
practises the moment they actually fell short on. Real calls are private, so
code anonymises everything before any prompt (``anonymise``):

* the call's speaker names and the team's names become "the customer" / "the rep";
* phone numbers and email addresses are removed;
* money amounts ("NPR 3,60,000", "4 lakh 20 hajar", "Rs 2,500") become "a
  different price", and other long numbers are dropped.

The persona builder (a model on the chat client) turns the anonymised moment
into a short persona; code checks its fields' lengths and anonymises its
output again, and the customer's name is chosen by code from a list of
fictional names, never by the model. Without a model the persona is built by
code: the opening line is the anonymised customer line itself, so the AI
customer still repeats the real objection.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .llm import LLMClient
from .pack import Pack

FICTIONAL_NAMES = ("Sita", "Hari", "Kamala", "Bishnu", "Gita", "Nabin", "Anita", "Suman", "Pooja", "Dipak")
DIFFICULTY = {1: "easy: soften after one good question", 2: "normal: soften after two good questions",
              3: "hard: stay doubtful until the rep asks about your needs and agrees a clear next step"}
MAX_FIELD = 200

_PHONE = re.compile(r"(?<!\w)(?:\+?977[-\s]?)?(?:9[678]\d[-\s]?\d{3}[-\s]?\d{4}|0\d{1,2}[-\s]?\d{6,7})(?!\w)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MONEY = re.compile(
    r"(?:\b(?:npr|nrs|rs\.?|rupees?)\s*)?\d[\d,]*(?:\.\d+)?\s*(?:lakh|lakhs|hajar|hazar|thousand|crore|लाख|हजार)"
    r"(?:\s*\d[\d,]*(?:\s*(?:hajar|hazar|thousand|हजार))?)?"
    r"|\b(?:npr|nrs|rs\.?|rupees?)\s*\d[\d,]*(?:\.\d+)?"
    r"|\b\d{1,3}(?:,\d{2,3}){1,}\b",
    re.I)
_LONG_NUMBER = re.compile(r"\b\d{5,}\b")


def anonymise(text: str, names: list[str] | tuple[str, ...] = ()) -> str:
    """Remove what identifies a real person or deal: names, phone numbers, emails and amounts."""
    out = _EMAIL.sub("an email address", text or "")
    out = _PHONE.sub("a phone number", out)
    out = _MONEY.sub("a different price", out)
    out = _LONG_NUMBER.sub("a number", out)
    for name in sorted({n for n in names if n and len(n) > 1}, key=len, reverse=True):
        for part in {name, *name.split()}:
            if len(part) > 2:
                out = re.sub(rf"(?<!\w){re.escape(part)}(?!\w)(?:\s+ji)?", "", out, flags=re.I)
    out = re.sub(r"\s+([,.?!])", r"\1", re.sub(r"\s{2,}", " ", out)).strip(" ,")
    return out


def _clean(text: Any, names, limit: int = MAX_FIELD) -> str:
    s = anonymise(" ".join(str(text or "").split()), names)
    return s[:limit].rsplit(" ", 1)[0] if len(s) > limit else s


def fictional_name(seed: str, avoid: list[str] | tuple[str, ...] = ()) -> str:
    """A name from the list, never one of the real names (or their parts) on the call or the team."""
    taken = {part.casefold() for n in avoid if n for part in n.split()}
    names = [n for n in FICTIONAL_NAMES if n.casefold() not in taken] or ["The customer"]
    return names[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(names)]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


PERSONA_SYSTEM = """You write a short role for an AI that will play a customer on a practice sales call.
The rep is practising one moment from a real call where they fell short. The customer must bring that moment back.
Use only what is given. Everything is anonymised: never add names, phone numbers or prices.
- about: who the customer is and their situation, one sentence.
- wants: what they care about most, one sentence.
- opening: the customer's first words on the practice call, restating the real concern (under 25 words, spoken).
- if_good: how they react when the rep handles it well (one sentence).
- if_poor: how they push back when the rep does not (one sentence).
Return only JSON."""

PERSONA_SCHEMA = _obj({"about": {"type": "string", "maxLength": 200}, "wants": {"type": "string", "maxLength": 200},
                       "opening": {"type": "string", "maxLength": 200}, "if_good": {"type": "string", "maxLength": 200},
                       "if_poor": {"type": "string", "maxLength": 200}})


def rules_persona(moment: dict[str, Any], pack: Pack, names: list[str], seed: str, difficulty: int = 2) -> dict[str, Any]:
    """A persona without a model: the real customer line, anonymised, and the pack's own words."""
    said = (moment.get("customer_said") or {}).get("quote") or ""
    crit = pack.criterion(moment["criterion"])
    opening = _clean(said, names) or "I'm not sure about this. Can you tell me more?"
    return {
        "name": fictional_name(seed, names), "about": f"A prospective customer of {pack.name}.",
        "wants": "A fair deal they understand before deciding.",
        "opening": opening,
        "if_good": "Answers the rep's questions and warms up.",
        "if_poor": "Repeats the concern and stays unconvinced.",
        "practise": crit.title if crit else moment["criterion"], "difficulty": difficulty,
        "by": "rules (no language model)",
    }


def build_persona(moment: dict[str, Any], pack: Pack, names: list[str], seed: str, llm: LLMClient | None = None,
                  difficulty: int = 2) -> dict[str, Any]:
    """The persona for a drill on ``moment``; ``names`` are the real names to keep out of every prompt."""
    base = rules_persona(moment, pack, names, seed, difficulty)
    if llm is None:
        return base
    crit = pack.criterion(moment["criterion"])
    said = _clean((moment.get("customer_said") or {}).get("quote"), names, 300)
    rep = _clean(moment["evidence"]["quote"], names, 300)
    user = (f"Business: {anonymise(pack.description or pack.name, names)}\n"
            f"Skill the rep is practising: {crit.title if crit else moment['criterion']}. {crit.description if crit else ''}\n"
            f"What the customer said on the real call: \"{said or '(not recorded)'}\"\n"
            f"What the rep answered: \"{rep}\"\nWhat fell short: {_clean(moment.get('why'), names)}")
    out = llm.complete_json(PERSONA_SYSTEM, user, PERSONA_SCHEMA, "persona", max_tokens=300)
    persona = dict(base)
    for key in ("about", "wants", "opening", "if_good", "if_poor"):
        value = _clean(out.get(key), names)
        if value:
            persona[key] = value
    persona["by"] = f"model:{llm.name}"
    return persona


OBJECTION_SYSTEM = """You write a short role for an AI that will play a customer on a practice sales call.
The rep is practising how to handle one concern customers of this business raise. The customer must raise it.
Use only what is given. Never add names, phone numbers or prices.
- about: who the customer is and their situation, one sentence.
- wants: what they care about most, one sentence.
- opening: the customer's first words on the practice call, raising the concern (under 25 words, spoken).
- if_good: how they react when the rep handles it well (one sentence).
- if_poor: how they push back when the rep does not (one sentence).
Return only JSON."""


def objection_persona(says: str, context: str | None, pack: Pack, criterion: str, names: list[str], seed: str,
                      llm: LLMClient | None = None, difficulty: int = 1, anonymised: bool = True) -> dict[str, Any]:
    """The persona for a Playground drill on one objection.

    ``anonymised`` is False for the pack's own objections (the business wrote them; nothing in them is a
    customer's), True for objections heard on real calls, which are cleaned like any call moment.
    """
    clean = (lambda text, limit=240: _clean(text, names, limit)) if anonymised else \
        (lambda text, limit=240: " ".join(str(text or "").split())[:limit])
    crit = pack.criterion(criterion)
    base = {
        "name": fictional_name(seed, names), "about": clean(context) or f"A prospective customer of {pack.name}.",
        "wants": "A fair deal they understand before deciding.",
        "opening": clean(says) or "I'm not sure about this. Can you tell me more?",
        "if_good": "Answers the rep's questions and warms up.",
        "if_poor": "Repeats the concern and stays unconvinced.",
        "practise": crit.title if crit else criterion, "difficulty": difficulty, "by": "rules (no language model)",
    }
    if llm is None:
        return base
    user = (f"Business: {pack.description or pack.name}\n"
            f"Skill the rep is practising: {crit.title if crit else criterion}. {crit.description if crit else ''}\n"
            f"The concern, in the customer's words: \"{clean(says, 300)}\"\n"
            f"Who raises it: {clean(context, 300) or '(not given)'}")
    out = llm.complete_json(OBJECTION_SYSTEM, user, PERSONA_SCHEMA, "persona", max_tokens=300)
    persona = dict(base)
    for key in ("about", "wants", "opening", "if_good", "if_poor"):
        value = _clean(out.get(key), names)  # the model's words are always cleaned
        if value:
            persona[key] = value
    persona["by"] = f"model:{llm.name}"
    return persona
