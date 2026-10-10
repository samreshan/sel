"""Score a CallReport against a hand-annotated gold file.

Gold files use the same shape as an analyzer's output (findings, claims,
commitments), plus ``due_status``/``due_date`` on commitments. A predicted
item matches a gold item of the same type when their cited segments overlap
(and, for commitments, the owner is the same). Matching is one-to-one.

These numbers are only as meaningful as the calls they're computed on; see
docs/evaluation.md for how the benchmark set should be built.
"""

from __future__ import annotations

from typing import Any

from .models import CallReport


def _match(pred: list[dict[str, Any]], gold: list[dict[str, Any]], same, overlaps) -> list[tuple[dict, dict]]:
    """One-to-one matching; each prediction takes the unused gold item it overlaps most."""
    pairs, used = [], set()
    for p in pred:
        best, best_score = None, 0.0
        for i, g in enumerate(gold):
            if i in used or not same(p, g):
                continue
            score = overlaps(p["evidence"]["segment_ids"], g["evidence"]["segment_ids"])
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            pairs.append((p, gold[best]))
            used.add(best)
    return pairs


def _prf(tp: int, n_pred: int, n_gold: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": n_pred - tp,
        "fn": n_gold - tp,
        "precision": round(tp / n_pred, 3) if n_pred else None,
        "recall": round(tp / n_gold, 3) if n_gold else None,
    }


def _time_overlap(report: CallReport, reference: dict[str, Any], slack: float = 0.75):
    """Match by time instead of segment id, for reports transcribed from audio.

    Gold annotations cite the reference transcript's segments; speech recognition
    produces different segments, so both are mapped to time spans.
    """
    pred_spans = {s.id: (s.start, s.end) for s in report.transcript.segments}
    gold_spans = {s["id"]: (s["start"], s["end"]) for s in reference["segments"]}

    def overlaps(p_ids: list[str], g_ids: list[str]) -> float:
        """Seconds of overlap (plus ``slack``, so near-misses still count a little); 0 if none."""
        best = 0.0
        for p in p_ids:
            for g in g_ids:
                if p in pred_spans and g in gold_spans:
                    (a, b), (c, d) = pred_spans[p], gold_spans[g]
                    best = max(best, min(b, d) - max(a, c) + slack)
        return best

    return overlaps


def score(report: CallReport, gold: dict[str, Any], reference: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score ``report`` against ``gold``.

    Pass ``reference`` (the transcript the gold file was annotated on) when the
    report's transcript came from speech recognition; items then match by time.
    """
    r = report.to_dict()
    out: dict[str, Any] = {"call_id": report.transcript.call_id, "pack_id": report.pack_id,
                           "matched_by": "time" if reference else "segment id"}
    overlaps = _time_overlap(report, reference) if reference else (lambda p, g: float(len(set(p) & set(g))))

    for kind in ("question", "objection", "coaching"):
        pred = [f for f in r["findings"] if f["kind"] == kind]
        g = [f for f in gold.get("findings", []) if f["kind"] == kind]
        out[f"findings.{kind}"] = _prf(len(_match(pred, g, lambda a, b: True, overlaps)), len(pred), len(g))

    pairs = _match(r["commitments"], gold.get("commitments", []), lambda a, b: _role(report, a["owner"]) == _role_gold(b), overlaps)
    c = _prf(len(pairs), len(r["commitments"]), len(gold.get("commitments", [])))
    c["due_status_agreement"] = _ratio(sum(p["due"]["status"] == g.get("due_status") for p, g in pairs), len(pairs))
    dated = [(p, g) for p, g in pairs if g.get("due_date")]
    c["due_date_agreement"] = _ratio(sum(p["due"]["date"] == g["due_date"] for p, g in dated), len(dated))
    # The failure Sel most needs to avoid: inventing a date the call never fixed.
    c["invented_dates"] = sum(p["due"]["status"] == "exact" and g.get("due_status") != "exact" for p, g in pairs)
    out["commitments"] = c

    pairs = _match(r["claims"], gold.get("claims", []), lambda a, b: True, overlaps)
    cl = _prf(len(pairs), len(r["claims"]), len(gold.get("claims", [])))
    cl["verdict_accuracy"] = _ratio(sum(p["verdict"] == g["verdict"] for p, g in pairs), len(pairs))
    # Telling a rep a claim is supported when it isn't is the costliest error.
    cl["false_supported"] = sum(p["verdict"] == "supported" and g["verdict"] != "supported" for p, g in pairs)
    out["claims"] = cl

    out["rejected_by_evidence_gate"] = len(r["rejected"])
    out["timings"] = r["meta"].get("timings", {})
    if "llm_usage" in r["meta"]:
        out["llm_usage"] = r["meta"]["llm_usage"]
    return out


def _role(report: CallReport, speaker_id: str) -> str:
    sp = report.transcript.speaker(speaker_id)
    return sp.role if sp else speaker_id


def _role_gold(g: dict[str, Any]) -> str:
    # Gold files name owners by the reference transcript's speaker ids; S1 is the rep there.
    return g.get("owner_role") or {"S1": "rep", "S2": "customer"}.get(g["owner"], g["owner"])


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None
