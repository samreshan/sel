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


def _match(pred: list[dict[str, Any]], gold: list[dict[str, Any]], same) -> list[tuple[dict, dict]]:
    pairs, used = [], set()
    for p in pred:
        for i, g in enumerate(gold):
            if i in used or not same(p, g):
                continue
            if set(p["evidence"]["segment_ids"]) & set(g["evidence"]["segment_ids"]):
                pairs.append((p, g))
                used.add(i)
                break
    return pairs


def _prf(tp: int, n_pred: int, n_gold: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": n_pred - tp,
        "fn": n_gold - tp,
        "precision": round(tp / n_pred, 3) if n_pred else None,
        "recall": round(tp / n_gold, 3) if n_gold else None,
    }


def score(report: CallReport, gold: dict[str, Any]) -> dict[str, Any]:
    r = report.to_dict()
    out: dict[str, Any] = {"call_id": report.transcript.call_id, "pack_id": report.pack_id}

    for kind in ("question", "objection", "coaching"):
        pred = [f for f in r["findings"] if f["kind"] == kind]
        g = [f for f in gold.get("findings", []) if f["kind"] == kind]
        out[f"findings.{kind}"] = _prf(len(_match(pred, g, lambda a, b: True)), len(pred), len(g))

    pairs = _match(r["commitments"], gold.get("commitments", []), lambda a, b: a["owner"] == b["owner"])
    c = _prf(len(pairs), len(r["commitments"]), len(gold.get("commitments", [])))
    c["due_status_agreement"] = _ratio(sum(p["due"]["status"] == g.get("due_status") for p, g in pairs), len(pairs))
    dated = [(p, g) for p, g in pairs if g.get("due_date")]
    c["due_date_agreement"] = _ratio(sum(p["due"]["date"] == g["due_date"] for p, g in dated), len(dated))
    # The failure Sel most needs to avoid: inventing a date the call never fixed.
    c["invented_dates"] = sum(p["due"]["status"] == "exact" and g.get("due_status") != "exact" for p, g in pairs)
    out["commitments"] = c

    pairs = _match(r["claims"], gold.get("claims", []), lambda a, b: True)
    cl = _prf(len(pairs), len(r["claims"]), len(gold.get("claims", [])))
    cl["verdict_accuracy"] = _ratio(sum(p["verdict"] == g["verdict"] for p, g in pairs), len(pairs))
    # Telling a rep a claim is supported when it isn't is the costliest error.
    cl["false_supported"] = sum(p["verdict"] == "supported" and g["verdict"] != "supported" for p, g in pairs)
    out["claims"] = cl

    out["rejected_by_evidence_gate"] = len(r["rejected"])
    out["timings"] = r["meta"].get("timings", {})
    return out


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None
