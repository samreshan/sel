"""Passage retrieval over a pack: split facts and documents, then rank with BM25.

Every passage is an exact substring of the text it came from, so a caller can
quote it verbatim and point at its source. The index is pure Python and
deterministic; nothing here calls a model.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .pack import Pack

K1 = 1.5
B = 0.75
# Query-time synonyms count for half a term so they cannot outweigh the words actually typed.
SYNONYM_WEIGHT = 0.5

STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "in", "is", "it", "its", "of", "on", "or", "the", "this", "that", "to", "was", "were", "with",
    # Romanized Nepali function words.
    "ko", "ma", "cha", "chha", "ra", "ni", "ta", "pani", "hamro", "tapai", "sanga", "sahit",
})

# Canonical term -> spoken or romanized variants. Applied to queries only, in
# either direction: a query containing any member pulls in the whole group.
# Packs may extend this dict.
SYNONYMS: dict[str, frozenset[str]] = {
    "warranty": frozenset({"waranti"}),
    "years": frozenset({"year", "barsa", "barsha", "yrs"}),
    "price": frozenset({"parcha", "mulya", "dam", "cost", "costs"}),
    "month": frozenset({"mahina"}),
}

# Letters, digits, and Devanagari combining marks (vowel signs, virama, etc.),
# so Devanagari words are not split apart. The danda (U+0964/U+0965) is excluded.
_TOKEN = re.compile(r"[\wऀ-ःऺ-ॏ॑-ॗॢॣ‌‍]+")
# Digits with Indian or Western thousands separators: 4,20,000 or 2,500.
_GROUPED_NUMBER = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{2,3})+(?!\d)(?!,\d)")
# "4 lakh 20 hajar", "4 lakh", "20 hajar", and the Devanagari words for them.
_NUM = r"\d+(?:\.\d+)?"
_AMOUNT = re.compile(
    rf"({_NUM})\s*(?:lakhs?\b|लाख)(?:\s*({_NUM})\s*(?:hajars?\b|हजार))?"
    rf"|({_NUM})\s*(?:hajars?\b|हजार)"
)

_HEADING = re.compile(r"#{1,6}(?:\s|$)")
_SEPARATOR = re.compile(r"\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?")
_SENTENCE_END = re.compile(r"\.\s+")


@dataclass
class Passage:
    source: str  # "docs/warranty.md" or "fact:inverter-warranty"; accepted by Pack.source_text
    text: str  # verbatim substring of pack.source_text(source)
    heading: str | None = None  # nearest markdown heading above it, for display


def tokenize(text: str) -> list[str]:
    """Casefolded word tokens, with thousands separators removed and stopwords dropped.

    Amounts written in lakh/hajar also yield the integer as an extra token.
    """
    folded = _GROUPED_NUMBER.sub(lambda m: m.group(0).replace(",", ""), text.casefold())
    tokens = _TOKEN.findall(folded) + _amount_tokens(folded)
    return [t for t in tokens if t not in STOPWORDS]


def collapse_amounts(text: str) -> str:
    """Rewrite "4 lakh 20 hajar" as "420000" (casefolded), so amounts compare as one number."""
    folded = _GROUPED_NUMBER.sub(lambda m: m.group(0).replace(",", ""), text.casefold())

    def value(m: re.Match[str]) -> str:
        if m.group(1):
            v = float(m.group(1)) * 100_000 + (float(m.group(2)) * 1_000 if m.group(2) else 0)
        else:
            v = float(m.group(3)) * 1_000
        return str(round(v))

    return _AMOUNT.sub(value, folded)


def _amount_tokens(folded: str) -> list[str]:
    out = []
    for m in _AMOUNT.finditer(folded):
        if m.group(1):
            value = float(m.group(1)) * 100_000
            if m.group(2):
                value += float(m.group(2)) * 1_000
        else:
            value = float(m.group(3)) * 1_000
        out.append(str(round(value)))
    return out


def split_passages(pack: Pack, max_chars: int = 600) -> list[Passage]:
    """One passage per fact, then passages from each document in pack order.

    Facts come first so that, on equal scores, an approved statement is listed
    before the document paragraph it came from.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    passages = [
        Passage(source=f"fact:{fact.id}", text=fact.statement.strip())
        for fact in pack.facts.values()
    ]
    for path, text in pack.docs.items():
        passages.extend(_document_passages(path, text, max_chars))
    return passages


def _document_passages(source: str, text: str, max_chars: int) -> list[Passage]:
    # Work on spans of the original text so every passage is an exact substring,
    # including line endings and any characters a join would have changed.
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line)

    spans: list[tuple[int, int, str | None]] = []  # (start, end, heading)
    heading: str | None = None
    para_start: int | None = None
    para_end = 0
    for i, line in enumerate(lines):
        start = starts[i]
        stripped = line.strip()
        if not stripped or _HEADING.match(stripped) or stripped.startswith("|"):
            if para_start is not None:
                spans.append((para_start, para_end, heading))
                para_start = None
        if not stripped:
            continue
        if _HEADING.match(stripped):
            heading = stripped.lstrip("#").strip() or None
        elif stripped.startswith("|"):
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            # Skip the header row (it sits above a separator) and the separator itself.
            if _is_separator(stripped) or _is_separator(next_line):
                continue
            spans.append((start, start + len(line), heading))
        else:
            if para_start is None:
                para_start = start
            para_end = start + len(line)
    if para_start is not None:
        spans.append((para_start, para_end, heading))

    passages = []
    for start, end, block_heading in spans:
        block = text[start:end].strip()
        for piece in _pieces(block, max_chars):
            passages.append(Passage(source=source, text=piece, heading=block_heading))
    return passages


def _is_separator(stripped: str) -> bool:
    return stripped.startswith("|") and _SEPARATOR.fullmatch(stripped) is not None


def _pieces(block: str, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]
    return _split_long(block, max_chars)


def _split_long(text: str, max_chars: int) -> list[str]:
    """Pack sentences greedily into pieces of at most max_chars; each piece is a substring."""
    sentences: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        sentences.append((start, m.start() + 1))
        start = m.end()
    sentences.append((start, len(text)))

    spans: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for s, e in sentences:
        if e - s > max_chars:
            if current is not None:
                spans.append(current)
                current = None
            spans.extend(_hard_split(text, s, e, max_chars))
        elif current is None:
            current = (s, e)
        elif e - current[0] <= max_chars:
            current = (current[0], e)
        else:
            spans.append(current)
            current = (s, e)
    if current is not None:
        spans.append(current)
    return [text[s:e] for s, e in spans]


def _hard_split(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Split one over-long sentence at whitespace, or mid-word if there is none."""
    spans = []
    while end - start > max_chars:
        cut = max_chars
        for i in range(max_chars - 1, 0, -1):
            if text[start + i].isspace():
                cut = i
                break
        piece_end = start + cut
        while piece_end > start and text[piece_end - 1].isspace():
            piece_end -= 1
        spans.append((start, piece_end))
        start += cut
        while start < end and text[start].isspace():
            start += 1
    spans.append((start, end))
    return spans


def _query_weights(tokens: list[str]) -> dict[str, float]:
    weights = {t: 1.0 for t in tokens}
    typed = set(tokens)
    for key, variants in SYNONYMS.items():
        group = {key, *variants}
        if group & typed:
            for term in group:
                weights.setdefault(term, SYNONYM_WEIGHT)
    return weights


class PassageIndex:
    """BM25 over passages. Heading text is indexed with its passage but not returned as part of it."""

    def __init__(self, passages: list[Passage]):
        self.passages = list(passages)
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)  # term -> (index, tf)
        self._lengths: list[int] = []
        for idx, passage in enumerate(self.passages):
            tokens = tokenize(passage.text)
            if passage.heading:
                tokens += tokenize(passage.heading)
            self._lengths.append(len(tokens))
            for term, tf in Counter(tokens).items():
                self._postings[term].append((idx, tf))
        total = sum(self._lengths)
        self._avg_len = total / len(self.passages) if self.passages else 0.0

    @classmethod
    def from_pack(cls, pack: Pack) -> PassageIndex:
        return cls(split_passages(pack))

    def search(self, query: str, k: int = 4) -> list[tuple[Passage, float]]:
        """Up to k passages with a positive score, best first; ties keep index order."""
        if k < 1 or not self.passages:
            return []
        n = len(self.passages)
        scores: dict[int, float] = defaultdict(float)
        for term, weight in _query_weights(tokenize(query)).items():
            postings = self._postings.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for idx, tf in postings:
                norm = 1 - B + B * self._lengths[idx] / self._avg_len
                scores[idx] += weight * idf * tf * (K1 + 1) / (tf + K1 * norm)
        ranked = sorted(
            ((idx, score) for idx, score in scores.items() if score > 0),
            key=lambda item: (-item[1], item[0]),
        )
        return [(self.passages[idx], score) for idx, score in ranked[:k]]
