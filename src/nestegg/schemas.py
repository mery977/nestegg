"""Data contracts. Money is always an integer number of cents: no float rounding errors, ever."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    id: str  # stable hash of (customer, date, amount, label, occurrence)
    customer_id: str
    date: date
    amount_cents: int  # negative = money out, positive = money in
    label: str  # as printed by the bank
    merchant: str = ""  # normalised merchant name, e.g. "NETFLIX"
    category: str = ""
    category_rule: str = ""  # why this category (explainability)


class Figure(BaseModel):
    """A number shown to a customer, with its proof: how it was computed and from which transactions."""

    name: str
    value: float  # euros, or a rate / count depending on `unit`
    unit: str  # "EUR", "%", "months", "count"
    formula: str
    transaction_ids: list[str] = Field(default_factory=list)


class Subscription(BaseModel):
    merchant: str
    category: str
    cadence: str  # weekly | monthly | yearly
    amount: float  # latest amount, euros
    previous_amount: float | None = None  # set when the price changed
    annual_cost: float
    occurrences: int
    first_date: date
    last_date: date
    next_expected: date
    active: bool
    transaction_ids: list[str]
