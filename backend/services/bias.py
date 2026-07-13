"""Adverse-impact / bias monitoring (Workstream F).

Selection-rate and four-fifths (80%) analysis on OUTCOMES, per tenant. Joins
hiring outcomes (applications) to voluntarily-provided EEO data — this is the
one legitimate, aggregate-only use of EEO data; it is never used in scoring.

Output is aggregate counts/rates only, with small groups suppressed (k-anonymity)
so the report can support an independent audit without exposing individuals.
"""

from __future__ import annotations

from db import repository

# A candidate is "selected" if a human advanced them (proceed/approved).
def _is_selected(app: dict) -> bool:
    return app.get("recruiter_decision") == "proceed" or app.get("status") == "approved"


def adverse_impact(min_group: int = 5) -> dict:
    apps = repository.list_all_applications()
    eeo = {r["candidate_id"]: (r.get("data") or {}) for r in repository.list_eeo_records()}

    # attribute -> group value -> {selected, total}
    attrs: dict[str, dict[str, dict[str, int]]] = {}
    for app in apps:
        data = eeo.get(app.get("candidate_id"))
        if not data:
            continue  # only candidates who voluntarily provided EEO data
        selected = _is_selected(app)
        for attr, value in data.items():
            if value in (None, ""):
                continue
            g = attrs.setdefault(attr, {}).setdefault(str(value), {"selected": 0, "total": 0})
            g["total"] += 1
            if selected:
                g["selected"] += 1

    report: dict[str, dict] = {}
    for attr, groups in attrs.items():
        usable = {v: g for v, g in groups.items() if g["total"] >= min_group}
        suppressed = sorted(v for v, g in groups.items() if g["total"] < min_group)
        rates = {v: (g["selected"] / g["total"] if g["total"] else 0.0) for v, g in usable.items()}
        max_rate = max(rates.values(), default=0.0)
        rows = []
        for v, g in sorted(usable.items()):
            rate = rates[v]
            ratio = (rate / max_rate) if max_rate else None
            rows.append({
                "group": v,
                "selected": g["selected"],
                "total": g["total"],
                "selection_rate": round(rate, 3),
                "impact_ratio": round(ratio, 3) if ratio is not None else None,
                "adverse": ratio is not None and ratio < 0.8,
            })
        report[attr] = {
            "groups": rows,
            "max_selection_rate": round(max_rate, 3),
            "suppressed_small_groups": suppressed,
            "four_fifths_violation": any(r["adverse"] for r in rows),
        }

    return {
        "min_group": min_group,
        "attributes": report,
        "note": (
            "Aggregate selection-rate / four-fifths analysis on outcomes. Groups "
            "smaller than min_group are suppressed. EEO data is segregated and is "
            "never used in candidate scoring."
        ),
    }
