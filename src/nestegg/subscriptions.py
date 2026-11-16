"""Recurring payments: subscriptions, forgotten ones, and price increases.

A series is a merchant debited at a regular interval (weekly, monthly or yearly) with similar amounts.
Price changes inside a series are detected rather than breaking it ("Netflix went from 13.49 to 15.99").
A series whose next expected payment is long overdue is reported as stopped, not as an active cost.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta

from .schemas import Subscription, Transaction

CADENCES = {"weekly": (5, 9, 7, 52), "monthly": (26, 35, 30, 12), "yearly": (350, 380, 365, 1)}
MIN_OCCURRENCES = {"weekly": 4, "monthly": 3, "yearly": 2}
EXCLUDED = {
    "Revenus",
    "Epargne",
    "Virements",
    "Retraits",
    "Alimentation",
    "Logement",
}  # regular, not subscriptions


def _cadence(dates: list[date]) -> str | None:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
    if not gaps:
        return None
    median = statistics.median(gaps)
    for name, (lo, hi, _, _) in CADENCES.items():
        if lo <= median <= hi and sum(lo <= g <= hi for g in gaps) >= 0.75 * len(gaps):
            return name
    return None


def detect(txs: list[Transaction], today: date | None = None) -> list[Subscription]:
    today = today or max((t.date for t in txs), default=date.today())
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in txs:
        if t.amount_cents < 0 and t.category not in EXCLUDED and t.merchant:
            by_merchant[t.merchant].append(t)

    found = []
    for merchant, items in by_merchant.items():
        items.sort(key=lambda t: t.date)
        cadence = _cadence([t.date for t in items])
        if not cadence or len(items) < MIN_OCCURRENCES[cadence]:
            continue
        amounts = [-t.amount_cents for t in items]
        # Similar amounts: all within 25% of the median (allows a price change, not random spending)
        med = statistics.median(amounts)
        if any(abs(a - med) > 0.25 * med for a in amounts):
            continue
        latest = amounts[-1]
        previous = next((a for a in reversed(amounts[:-1]) if a != latest), None)
        _, _, step, per_year = CADENCES[cadence]
        next_expected = items[-1].date + timedelta(days=step)
        active = (today - next_expected).days <= step // 2 + 5
        found.append(
            Subscription(
                merchant=merchant,
                category=items[-1].category,
                cadence=cadence,
                amount=latest / 100,
                previous_amount=previous / 100 if previous is not None else None,
                annual_cost=round(latest * per_year / 100, 2) if active else 0.0,
                occurrences=len(items),
                first_date=items[0].date,
                last_date=items[-1].date,
                next_expected=next_expected,
                active=active,
                transaction_ids=[t.id for t in items],
            )
        )
    return sorted(found, key=lambda s: -s.annual_cost)
