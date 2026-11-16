"""Savings goals and regulated-savings simulation, computed exactly as French banks do.

Interest on Livret A / LDDS / LEP is computed by fortnight ("quinzaine"): a deposit earns from the 1st or
16th that follows it, a withdrawal stops earning from the 1st or 16th that precedes it, and interest is
added to the balance once a year, on 31 December. The ceiling caps deposits (interest may exceed it).
Rates come from data/rates.json, with their date and source.

This module informs and simulates. It never recommends a financial product: that is regulated advice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from importlib import resources

from .schemas import Figure


def load_rates() -> dict:
    return json.loads(resources.files("nestegg").joinpath("data/rates.json").read_text(encoding="utf-8"))


def _fortnight_index(d: date) -> int:
    """Index of the fortnight (0..23) of a date within its year."""
    return (d.month - 1) * 2 + (0 if d.day < 16 else 1)


@dataclass
class Simulation:
    final_balance: float
    total_deposits: float
    total_interest: float
    capped_deposits: float  # deposits refused because the ceiling was reached
    yearly: list[dict]


def simulate_regulated(
    product: str,
    start: date,
    months: int,
    monthly_deposit: float,
    initial_balance: float = 0.0,
    rate_pct: float | None = None,
) -> Simulation:
    """`months` monthly deposits on the 1st of each month from `start`; balance shown at 31 December."""
    spec = load_rates()["products"][product]
    rate = (spec["rate_pct"] if rate_pct is None else rate_pct) / 100
    ceiling = spec["ceiling_eur"] * 100
    balance = round(initial_balance * 100)  # cents on the account (deposits + capitalised interest)
    earning = 0  # cents earning interest in the current fortnight
    pending: dict[int, int] = {}  # absolute fortnight -> cents that start earning then
    start_fn = start.year * 24 + _fortnight_index(start)
    pending[start_fn] = balance  # money already there earns from the start date
    deposits_left = months
    total_dep = capped = 0
    yearly, total_interest, interest = [], 0, 0.0
    fn = start.year * 24 + _fortnight_index(start)
    last_month = start.month - 1 + max(months, 1) - 1  # months after January of the start year
    last_fn = (start.year + last_month // 12) * 24 + 23  # simulate through 31 December of that year
    while fn <= last_fn:
        year, q = divmod(fn, 24)
        if q % 2 == 0 and deposits_left and fn >= start_fn and not (fn == start_fn and start.day > 1):
            # deposit on the 1st of the month; the ceiling applies to the balance, interest included
            amount = round(monthly_deposit * 100)
            accepted = max(0, min(amount, ceiling - balance))
            capped += amount - accepted
            total_dep += accepted
            balance += accepted
            pending[fn + 1] = pending.get(fn + 1, 0) + accepted  # earns from the 16th
            deposits_left -= 1
        earning += pending.pop(fn, 0)
        interest += earning * rate / 24
        if q == 23:  # 31 December: interest is added to the balance and starts earning next year
            cents = round(interest)
            balance += cents
            earning += cents
            total_interest += cents
            yearly.append({"year": year, "balance": balance / 100, "interest": cents / 100})
            interest = 0.0
        fn += 1
    return Simulation(
        final_balance=balance / 100,
        total_deposits=total_dep / 100,
        total_interest=total_interest / 100,
        capped_deposits=capped / 100,
        yearly=yearly,
    )


def goal_plan(
    target_eur: float, deadline: date, today: date, already_saved_eur: float, savings_capacity: Figure
) -> list[Figure]:
    """Monthly effort to reach a goal, compared with what the customer actually saves."""
    months = (deadline.year - today.year) * 12 + (deadline.month - today.month)
    if months <= 0:
        raise ValueError("the deadline must be at least one month away")
    missing = max(0.0, target_eur - already_saved_eur)
    monthly = round(missing / months, 2)
    capacity = savings_capacity.value
    figures = [
        Figure(
            name="goal_monthly_effort",
            value=monthly,
            unit="EUR",
            formula=f"({target_eur:.2f} - {already_saved_eur:.2f} already saved) / {months} months",
        ),
        Figure(
            name="goal_months",
            value=months,
            unit="months",
            formula=f"months from {today:%Y-%m} to {deadline:%Y-%m}",
        ),
        Figure(
            name="goal_share_of_capacity",
            value=round(monthly / capacity * 100, 1) if capacity > 0 else 0.0,
            unit="%",
            formula=f"{monthly:.2f} / savings capacity {capacity:.2f}",
            transaction_ids=savings_capacity.transaction_ids,
        ),
    ]
    if capacity < monthly:
        figures.append(
            Figure(
                name="goal_gap",
                value=round(monthly - capacity, 2),
                unit="EUR",
                formula=f"{monthly:.2f} needed - {capacity:.2f} saved per month today",
                transaction_ids=savings_capacity.transaction_ids,
            )
        )
    return figures
