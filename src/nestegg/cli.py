"""Command line.

  nestegg demo                                  fictional statements for 3 customers, imported
  nestegg import STATEMENT.csv --customer C001  import a bank export (any French bank layout)
  nestegg report --customer C001                spending, subscriptions, drifts, savings capacity
  nestegg plan --customer C001 --target 5000 --by 2027-12
  nestegg simulate --product livret_a --monthly 200 --months 24
  nestegg mask --customer C001                  what a language model would see (personal data masked)
  nestegg forget --customer C001                erase every row of a customer (GDPR article 17)
  nestegg ask --customer C001 "Quels sont mes abonnements ?"   one question to the assistant
  nestegg chat --customer C001                  conversation in the terminal (streamlit run ui/app.py for the web)

Model: --provider offline (default, no key) | azure | openai | anthropic | gemini | local

The database is `nestegg.db` in the current folder (NESTEGG_DB to change it). It never leaves your machine.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import analytics, subscriptions
from .demo import PERSONAS, build_demo
from .importers import read_csv
from .pii import Vault, redact
from .planner import goal_plan, load_rates, simulate_regulated
from .store import Store


def _eur(v: float) -> str:
    return f"{v:,.2f} EUR".replace(",", " ")


def ensure_demo(db: str) -> None:
    """Load the fictional customers into an empty database (used by the web interface)."""
    store = Store(db)
    try:
        if not store.customers():
            for path, persona in zip(build_demo(), PERSONAS, strict=True):
                store.for_customer(persona.customer_id).add(read_csv(path, persona.customer_id)[0])
    finally:
        store.close()


def print_response(r) -> None:
    print(r.answer)
    if r.refused:
        print(f"\n[refused: {r.refusal_reason}]")
        return
    print(
        f"\n[{'every figure verified' if r.numbers_verified else 'model text replaced by verified figures'}"
        f" · {r.llm_calls} model call(s) · {r.latency_ms} ms · tools: {[t['tool'] for t in r.tool_calls]}]"
    )
    for e in r.guard_events:
        print(f"[guardrail] {e}")
    for ref, f in r.figures.items():
        print(f"  {ref}  {f.value:>12,.2f} {f.unit:<6} {f.formula}  ({len(f.transaction_ids)} tx)")


def _store() -> Store:
    return Store(os.environ.get("NESTEGG_DB", "nestegg.db"))


def cmd_report(data, as_json: bool) -> int:
    txs = data.transactions()
    if not txs:
        print(f"no transactions for customer {data.customer_id}")
        return 1
    full = analytics.complete_months(txs)
    last = full[-1] if full else analytics.months_covered(txs)[-1]
    flows = {f["month"]: f for f in analytics.monthly_cashflow(txs)}
    report = {
        "customer": data.customer_id,
        "period": f"{txs[0].date} to {txs[-1].date}",
        "last_complete_month": last,
        "cashflow_last_month": {k: v.model_dump() for k, v in flows[last].items() if k != "month"},
        "spending_last_month": [f.model_dump() for f in analytics.spending_by_category(txs, last)],
        "savings_capacity": analytics.savings_capacity(txs).model_dump(),
        "subscriptions": [s.model_dump(mode="json") for s in subscriptions.detect(txs)],
        "drifts": [f.model_dump() for f in analytics.drifts(txs)],
        "bank_fees": analytics.bank_fees(txs).model_dump(),
        "to_categorise": [t.label for t in txs if t.category == "A categoriser"],
    }
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0
    cf = flows[last]
    print(f"Customer {data.customer_id} · {report['period']} · {len(txs)} transactions\n")
    print(
        f"Last complete month ({last}): income {_eur(cf['income'].value)}, spending {_eur(cf['spending'].value)}, "
        f"left {_eur(cf['net'].value)}, moved to savings {_eur(cf['saved'].value)}"
    )
    cap = analytics.savings_capacity(txs)
    print(f"Savings capacity: {_eur(cap.value)} per month  ({cap.formula})\n")
    print(f"Spending in {last}:")
    for f in report["spending_last_month"]:
        print(f"  {f['name'].split(':', 1)[1]:<18} {_eur(f['value']):>14}   ({len(f['transaction_ids'])} tx)")
    print("\nSubscriptions:")
    for s in subscriptions.detect(txs):
        change = (
            f"  price up from {s.previous_amount:.2f}" if s.previous_amount and s.amount > s.previous_amount else ""
        )
        state = f"{_eur(s.annual_cost)} / year" if s.active else f"stopped since {s.last_date}"
        print(f"  {s.merchant:<26} {s.amount:>7.2f} {s.cadence:<8} {state}{change}")
    if report["drifts"]:
        print("\nSpending drifts:")
        for f in analytics.drifts(txs):
            print(f"  {f.name.split(':')[1]:<18} +{f.value:.0f} %  ({f.formula})")
    print(f"\nBank fees over the period: {_eur(report['bank_fees']['value'])}")
    if report["to_categorise"]:
        print(f"To categorise ({len(report['to_categorise'])}): the agent will propose a category in step 2.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nestegg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    p = sub.add_parser("import")
    p.add_argument("statement")
    p.add_argument("--customer", required=True)
    p = sub.add_parser("report")
    p.add_argument("--customer", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("plan")
    p.add_argument("--customer", required=True)
    p.add_argument("--target", type=float, required=True, help="amount to reach, in euros")
    p.add_argument("--by", required=True, help="deadline, YYYY-MM")
    p.add_argument("--saved", type=float, default=0.0, help="already saved for this goal")
    p = sub.add_parser("simulate")
    p.add_argument("--product", default="livret_a", choices=sorted(load_rates()["products"]))
    p.add_argument("--monthly", type=float, required=True)
    p.add_argument("--months", type=int, required=True)
    p.add_argument("--initial", type=float, default=0.0)
    p.add_argument("--start", default=None, help="YYYY-MM-DD, default: 1st of next month")
    p = sub.add_parser("mask")
    p.add_argument("--customer", required=True)
    p.add_argument("--limit", type=int, default=15)
    p = sub.add_parser("forget")
    p.add_argument("--customer", required=True)
    for name in ("ask", "chat"):
        p = sub.add_parser(name)
        p.add_argument("--customer", required=True)
        p.add_argument(
            "--provider", default=None, help="offline (default) | azure | openai | anthropic | gemini | local"
        )
        p.add_argument("--model", default=None)
        if name == "ask":
            p.add_argument("question")
    args = parser.parse_args(argv)

    if args.cmd == "simulate":
        today = date.today()
        start = (
            date.fromisoformat(args.start)
            if args.start
            else date(today.year + today.month // 12, today.month % 12 + 1, 1)
        )
        rates = load_rates()
        spec = rates["products"][args.product]
        s = simulate_regulated(args.product, start, args.months, args.monthly, args.initial)
        print(
            f"{spec['name']} at {spec['rate_pct']} % (rate as of {rates['as_of']}, next revision "
            f"{rates['next_revision']}, source: {rates['source']})"
        )
        print("Simulation with today's rate, which the State revises twice a year: not a promise.\n")
        for y in s.yearly:
            print(f"  31/12/{y['year']}: balance {_eur(y['balance'])}, interest of the year {_eur(y['interest'])}")
        print(f"\nDeposits {_eur(s.total_deposits)}, interest {_eur(s.total_interest)}, final {_eur(s.final_balance)}")
        if s.capped_deposits:
            print(f"Ceiling of {_eur(spec['ceiling_eur'])} reached: {_eur(s.capped_deposits)} could not be deposited.")
        return 0

    store = _store()
    if args.cmd == "demo":
        for path, persona in zip(build_demo(), PERSONAS, strict=True):
            txs, rep = read_csv(path, persona.customer_id)
            added = store.for_customer(persona.customer_id).add(txs)
            print(f"{persona.customer_id}: {path} ({rep.layout['delimiter']!r} separated), {added} transactions added")
        print("\nNext: nestegg report --customer C001")
        return 0
    data = store.for_customer(args.customer)
    if args.cmd == "import":
        txs, rep = read_csv(Path(args.statement), args.customer)
        added = data.add(txs)
        print(f"{rep.rows} rows read, {rep.imported} valid, {added} new (duplicates ignored). Layout: {rep.layout}")
        for line, reason in rep.skipped:
            print(f"  line {line} skipped: {reason}")
        return 0
    if args.cmd == "report":
        return cmd_report(data, args.json)
    if args.cmd == "plan":
        txs = data.transactions()
        y, m = (int(x) for x in args.by.split("-"))
        today = txs[-1].date if txs else date.today()
        for f in goal_plan(args.target, date(y, m, 1), today, args.saved, analytics.savings_capacity(txs)):
            unit = "" if f.unit == "months" else f" {f.unit}"
            print(f"{f.name:<24} {f.value:>10,.2f}{unit}   ({f.formula})")
        return 0
    if args.cmd == "mask":
        vault = Vault(args.customer)
        for t in data.transactions()[: args.limit * 20]:
            masked = redact(t.label, vault)
            if masked != t.label:
                print(f"  {t.label}\n  -> {masked}\n")
                args.limit -= 1
                if not args.limit:
                    break
        print(f"{len(vault)} personal values replaced by tokens; the mapping stays on the server.")
        return 0
    if args.cmd in ("ask", "chat"):
        from .agent import Assistant
        from .llm import make_llm

        assistant = Assistant(data, make_llm(args.provider, args.model))
        if args.cmd == "ask":
            print_response(assistant.ask(args.question))
            return 0
        print("Nestegg assistant (empty line to quit). Every figure is computed by code and verified.\n")
        while True:
            try:
                q = input("you > ").strip()
            except EOFError:
                break
            if not q:
                break
            print()
            print_response(assistant.ask(q))
            print()
        return 0
    if args.cmd == "forget":
        print(f"{data.erase()} transactions of customer {args.customer} deleted.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
