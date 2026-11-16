"""Deterministic analyses. Each result is a Figure: value + formula + the transactions it comes from.

These functions are the agent's tools: the language model never adds, averages or projects numbers itself.
Transfers to savings accounts are neither spending nor income: they are counted as savings.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date

from .schemas import Figure, Transaction

NOT_SPENDING = {"Epargne", "Revenus"}


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _eur(cents: int) -> float:
    return round(cents / 100, 2)


def months_covered(txs: list[Transaction]) -> list[str]:
    return sorted({month_key(t.date) for t in txs})


def spending_by_category(txs: list[Transaction], month: str | None = None) -> list[Figure]:
    """Money out per category (refunds in the same category are netted), largest first."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in txs:
        if (month is None or month_key(t.date) == month) and t.category not in NOT_SPENDING:
            groups[t.category].append(t)
    figures = []
    for cat, items in groups.items():
        spent = -sum(t.amount_cents for t in items)
        if spent > 0:
            figures.append(
                Figure(
                    name=f"spending:{cat}",
                    value=_eur(spent),
                    unit="EUR",
                    formula=f"- sum of {len(items)} transactions in '{cat}'" + (f" in {month}" if month else ""),
                    transaction_ids=[t.id for t in items],
                )
            )
    return sorted(figures, key=lambda f: -f.value)


def monthly_cashflow(txs: list[Transaction]) -> list[dict]:
    """Per month: income, spending, savings transfers, and what is left (net)."""
    out = []
    for m in months_covered(txs):
        items = [t for t in txs if month_key(t.date) == m]
        income = [t for t in items if t.category == "Revenus"]
        saved = [t for t in items if t.category == "Epargne"]
        spent = [t for t in items if t.category not in NOT_SPENDING]
        out.append(
            {
                "month": m,
                "income": Figure(
                    name=f"income:{m}",
                    value=_eur(sum(t.amount_cents for t in income)),
                    unit="EUR",
                    formula="sum of credits categorised 'Revenus'",
                    transaction_ids=[t.id for t in income],
                ),
                "spending": Figure(
                    name=f"spending:{m}",
                    value=_eur(-sum(t.amount_cents for t in spent)),
                    unit="EUR",
                    formula="- sum of debits and refunds outside 'Revenus' and 'Epargne'",
                    transaction_ids=[t.id for t in spent],
                ),
                "saved": Figure(
                    name=f"saved:{m}",
                    value=_eur(-sum(t.amount_cents for t in saved)),
                    unit="EUR",
                    formula="transfers to savings accounts",
                    transaction_ids=[t.id for t in saved],
                ),
                "net": Figure(
                    name=f"net:{m}",
                    value=_eur(sum(t.amount_cents for t in items if t.category != "Epargne")),
                    unit="EUR",
                    formula="income - spending (savings transfers excluded)",
                    transaction_ids=[t.id for t in items if t.category != "Epargne"],
                ),
            }
        )
    return out


def complete_months(txs: list[Transaction]) -> list[str]:
    """Months fully covered by the statement (the first and last ones are often partial)."""
    months = months_covered(txs)
    if len(months) <= 2:
        return months
    first, last = min(t.date for t in txs), max(t.date for t in txs)
    keep = months[:]
    if first.day > 3:
        keep.remove(months[0])
    if last.day < 25:
        keep.remove(months[-1])
    return keep


def savings_capacity(txs: list[Transaction], last_n: int = 6) -> Figure:
    """What the customer can save per month: median of (income - spending) over the last complete months.
    The median, not the mean, so that one exceptional month (a bonus, a holiday) does not distort it."""
    full = complete_months(txs)[-last_n:]
    flows = [f for f in monthly_cashflow(txs) if f["month"] in full]
    if not flows:
        return Figure(name="savings_capacity", value=0.0, unit="EUR", formula="no complete month")
    values = [f["net"].value for f in flows]  # what was left each month, whether it was then saved or not
    ids = [i for f in flows for i in f["net"].transaction_ids]
    return Figure(
        name="savings_capacity",
        value=round(statistics.median(values), 2),
        unit="EUR",
        formula=f"median over {len(flows)} complete months ({full[0]} to {full[-1]}) of income - spending",
        transaction_ids=ids,
    )


def drifts(txs: list[Transaction], threshold: float = 0.30, min_eur: float = 30.0) -> list[Figure]:
    """Categories where the last complete month is above the average of the 3 months before, by at least
    `threshold` (relative) and `min_eur` (absolute), so that small noise is not reported."""
    full = complete_months(txs)
    if len(full) < 4:
        return []
    last, previous = full[-1], full[-4:-1]
    by_month = {m: {f.name.split(":", 1)[1]: f for f in spending_by_category(txs, m)} for m in [last, *previous]}
    out = []
    for cat, fig in by_month[last].items():
        if cat in {"Virements", "Retraits"}:  # money moved, not consumption: no drift reported
            continue
        base_values = [by_month[m][cat].value if cat in by_month[m] else 0.0 for m in previous]
        base = sum(base_values) / 3
        if fig.value - base >= min_eur and (base == 0 or (fig.value - base) / base >= threshold):
            change = (fig.value - base) / base * 100 if base else 100.0
            ids = fig.transaction_ids + [
                i for m in previous if cat in by_month[m] for i in by_month[m][cat].transaction_ids
            ]
            out.append(
                Figure(
                    name=f"drift:{cat}:{last}",
                    value=round(change, 1),
                    unit="%",
                    formula=f"{last}: {fig.value:.2f} vs avg {base:.2f} EUR ({previous[0]}..{previous[-1]})",
                    transaction_ids=ids,
                )
            )
    return sorted(out, key=lambda f: -f.value)


def bank_fees(txs: list[Transaction]) -> Figure:
    fees = [t for t in txs if t.category == "Frais bancaires"]
    return Figure(
        name="bank_fees",
        value=_eur(-sum(t.amount_cents for t in fees)),
        unit="EUR",
        formula=f"sum of {len(fees)} bank fee transactions",
        transaction_ids=[t.id for t in fees],
    )
