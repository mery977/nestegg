"""Every figure is exact and provable: recomputing it from its own transactions gives the same value."""

import json
from datetime import date

import pytest

from nestegg import analytics, subscriptions
from nestegg.categorize import UNCATEGORISED, categorise, normalise_merchant
from nestegg.planner import goal_plan, load_rates, simulate_regulated
from nestegg.schemas import Figure


@pytest.mark.parametrize(
    "label, merchant",
    [
        ("CB NETFLIX.COM 12/10 X4974", "NETFLIX.COM"),
        ("PRLV SEPA FREE MOBILE ECH/151026", "FREE MOBILE"),
        ("PAIEMENT PAR CARTE X1234 MONOPRIX PARIS 15", "MONOPRIX PARIS 15"),
    ],
)
def test_merchant_normalisation(label, merchant):
    assert normalise_merchant(label) == merchant


def test_categories_are_explained_and_unknowns_are_not_guessed():
    assert categorise("CB NETFLIX.COM", -1349) == ("Abonnements", "keyword 'NETFLIX'")
    assert categorise("VIR LIVRET A EPARGNE", -20000)[0] == "Epargne"
    assert categorise("VIR SEPA RECU SALAIRE OCTOBRE", 265000)[0] == "Revenus"
    assert categorise("CB LE PETIT ZINC", -4250) == (UNCATEGORISED, "no rule matched")


@pytest.mark.parametrize("customer", ["C001", "C002", "C003"])
def test_subscriptions_match_the_ground_truth(store, demo_files, customer):
    out, _ = demo_files
    truth = json.loads((out / f"{customer.lower()}.truth.json").read_text(encoding="utf-8"))["subscriptions"]
    found = {s.merchant: s for s in subscriptions.detect(store.for_customer(customer).transactions())}
    assert set(found) == set(truth)
    for merchant, expected in truth.items():
        s = found[merchant]
        assert (s.cadence, s.amount, s.active) == (expected["cadence"], expected["amount"], expected["active"])
        if "previous_amount" in expected:
            assert s.previous_amount == expected["previous_amount"]


def _recompute(fig: Figure, by_id: dict, sign: int = -1) -> float:
    return round(sign * sum(by_id[i].amount_cents for i in fig.transaction_ids) / 100, 2)


def test_every_figure_is_reproducible_from_its_own_transactions(store):
    txs = store.for_customer("C001").transactions()
    by_id = {t.id: t for t in txs}
    for f in analytics.spending_by_category(txs, "2026-10"):
        assert _recompute(f, by_id) == f.value
    for month in analytics.monthly_cashflow(txs):
        assert _recompute(month["income"], by_id, +1) == month["income"].value
        assert _recompute(month["spending"], by_id) == month["spending"].value
        assert _recompute(month["net"], by_id, +1) == month["net"].value
        assert round(month["income"].value - month["spending"].value, 2) == month["net"].value
    assert _recompute(analytics.bank_fees(txs), by_id) == 58.0


def test_drift_detects_the_restaurant_surge_and_ignores_transfers(store):
    drifts = {f.name.split(":")[1]: f for f in analytics.drifts(store.for_customer("C001").transactions())}
    assert "Restaurants" in drifts and drifts["Restaurants"].value > 30
    assert "Virements" not in drifts


def test_savings_capacity_uses_complete_months_only(store):
    cap = analytics.savings_capacity(store.for_customer("C001").transactions())
    assert cap.value > 0 and "complete months (2026-05 to 2026-10)" in cap.formula


def test_rates_are_dated_and_sourced():
    rates = load_rates()
    assert rates["as_of"] and rates["source"].startswith("https://") and rates["next_revision"]
    assert rates["products"]["livret_a"]["rate_pct"] == 1.7


def test_livret_a_interest_follows_the_fortnight_rule():
    # A full year at the ceiling: 22,950 x 1.7 % = 390.15
    assert simulate_regulated("livret_a", date(2027, 1, 1), 0, 0, initial_balance=22950).total_interest == 390.15
    # 100 on the 1st of each month earns from the 16th: 23 + 21 + ... + 1 = 144 fortnights of 100
    s = simulate_regulated("livret_a", date(2027, 1, 1), 12, 100)
    assert s.total_interest == round(100 * 0.017 / 24 * 144, 2) and s.total_deposits == 1200


def test_the_ceiling_caps_deposits_interest_included():
    s = simulate_regulated("livret_a", date(2027, 1, 1), 24, 1000, initial_balance=20000)
    assert s.total_deposits == 2950 and s.capped_deposits == 24000 - 2950
    assert s.yearly[0]["interest"] == 383.95


def test_goal_plan_compares_effort_with_real_capacity():
    cap = Figure(name="savings_capacity", value=300, unit="EUR", formula="test", transaction_ids=["a"])
    figs = {f.name: f for f in goal_plan(6000, date(2027, 12, 1), date(2026, 12, 1), 1200, cap)}
    assert figs["goal_monthly_effort"].value == 400.0 and figs["goal_gap"].value == 100.0
    assert figs["goal_gap"].transaction_ids == ["a"]
    with pytest.raises(ValueError):
        goal_plan(1000, date(2026, 1, 1), date(2026, 1, 1), 0, cap)
