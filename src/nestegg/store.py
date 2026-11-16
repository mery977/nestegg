"""Storage with customer isolation enforced by code, not by prompts.

`Store` is the only object that touches the database, and it hands out `CustomerData` views bound to one
customer. Every query of a view filters on that customer: there is no method that takes another customer's
id, so a manipulated agent cannot read someone else's data, whatever it asks. Erasure (GDPR article 17)
deletes every row of a customer.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from .categorize import enrich
from .schemas import Transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    label TEXT NOT NULL,
    merchant TEXT NOT NULL,
    category TEXT NOT NULL,
    category_rule TEXT NOT NULL,
    PRIMARY KEY (customer_id, id)
);
CREATE INDEX IF NOT EXISTS tx_customer_date ON transactions (customer_id, date);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def for_customer(self, customer_id: str) -> CustomerData:
        if not customer_id or not customer_id.strip():
            raise ValueError("a customer id is required")
        return CustomerData(self._db, customer_id)

    def customers(self) -> list[str]:
        """Admin view: ids only, never data."""
        return [r[0] for r in self._db.execute("SELECT DISTINCT customer_id FROM transactions ORDER BY 1")]

    def close(self) -> None:
        self._db.close()


class CustomerData:
    """Everything the agent may read, for one customer only."""

    def __init__(self, db: sqlite3.Connection, customer_id: str):
        self._db = db
        self.customer_id = customer_id

    def add(self, transactions: list[Transaction]) -> int:
        """Insert (idempotent: re-importing the same statement adds nothing). Returns rows added."""
        foreign = {t.customer_id for t in transactions} - {self.customer_id}
        if foreign:
            raise PermissionError("transactions of another customer cannot be added to this one")
        enrich(transactions)
        with self._db:
            before = self.count()
            self._db.executemany(
                "INSERT OR IGNORE INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        t.id,
                        self.customer_id,
                        t.date.isoformat(),
                        t.amount_cents,
                        t.label,
                        t.merchant,
                        t.category,
                        t.category_rule,
                    )
                    for t in transactions
                ],
            )
        return self.count() - before

    def count(self) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM transactions WHERE customer_id = ?", (self.customer_id,)
        ).fetchone()[0]

    def transactions(
        self, start: date | None = None, end: date | None = None, category: str | None = None
    ) -> list[Transaction]:
        sql = "SELECT * FROM transactions WHERE customer_id = ?"
        args: list = [self.customer_id]
        if start:
            sql, args = sql + " AND date >= ?", [*args, start.isoformat()]
        if end:
            sql, args = sql + " AND date <= ?", [*args, end.isoformat()]
        if category:
            sql, args = sql + " AND category = ?", [*args, category]
        with closing(self._db.execute(sql + " ORDER BY date, id", args)) as cur:
            return [
                Transaction(
                    id=r[0],
                    customer_id=r[1],
                    date=date.fromisoformat(r[2]),
                    amount_cents=r[3],
                    label=r[4],
                    merchant=r[5],
                    category=r[6],
                    category_rule=r[7],
                )
                for r in cur
            ]

    def set_category(self, transaction_id: str, category: str, rule: str) -> bool:
        with self._db:
            cur = self._db.execute(
                "UPDATE transactions SET category = ?, category_rule = ? WHERE customer_id = ? AND id = ?",
                (category, rule, self.customer_id, transaction_id),
            )
        return cur.rowcount == 1

    def erase(self) -> int:
        """Delete everything about this customer. Returns the number of rows deleted."""
        with self._db:
            return self._db.execute("DELETE FROM transactions WHERE customer_id = ?", (self.customer_id,)).rowcount
