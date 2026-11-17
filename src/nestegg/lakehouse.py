"""Nestegg on a lakehouse (Databricks / Spark): the same rules as the Python package, at bank scale.

    bronze   raw statement lines as exported by the bank: text dates and French-formatted amounts
    silver   parsed, typed, deduplicated, merchant + explainable category; unreadable lines quarantined
    gold     monthly cashflow, spending per category, subscriptions, savings capacity, per customer

The business logic is not rewritten for Spark: the same functions (parse_amount, categorise, subscriptions.detect,
analytics.savings_capacity) run inside pandas UDFs, so a figure computed on a laptop and on Databricks is the
same figure. A test checks it. On Databricks the package is installed as a wheel on every node (see databricks.yml).
"""

from __future__ import annotations

import random
from datetime import date

import pandas as pd

BRONZE_SCHEMA = "customer_id string, line_no int, date_raw string, amount_raw string, label string, source string"
QUARANTINE_SCHEMA = "customer_id string, line_no int, date_raw string, amount_raw string, label string, reason string"
SILVER_SCHEMA = (
    "transaction_id string, customer_id string, date date, amount_cents long, label string, merchant string, "
    "category string, category_rule string"
)
SUBSCRIPTIONS_SCHEMA = (
    "customer_id string, merchant string, category string, cadence string, amount double, previous_amount double, "
    "annual_cost double, occurrences int, first_date date, last_date date, next_expected date, active boolean"
)
CAPACITY_SCHEMA = "customer_id string, savings_capacity double, months_used int, formula string"


# --- bronze ------------------------------------------------------------------------------------------------


def _bronze_rows(customer_ids: list[str], seed: int, start: date, months: int, dirty_rate: float) -> pd.DataFrame:
    from .demo import generate, random_persona

    out = []
    for cid in customer_ids:
        rows, _ = generate(random_persona(cid, seed), start=start, months=months, seed=seed)
        rng = random.Random(f"dirty-{seed}-{cid}")
        for n, (d, amount, label) in enumerate(rows, start=1):
            date_raw, amount_raw = d.strftime("%d/%m/%Y"), f"{amount:.2f}".replace(".", ",")
            if rng.random() < dirty_rate:  # what real files contain: impossible dates, broken amounts
                date_raw, amount_raw = rng.choice([("31/02/2026", amount_raw), (date_raw, "12,3O"), ("", amount_raw)])
            out.append((cid, n, date_raw, amount_raw, label, "synthetic-generator"))
    return pd.DataFrame(out, columns=["customer_id", "line_no", "date_raw", "amount_raw", "label", "source"])


def generate_bronze(
    spark,
    n_customers: int,
    seed: int = 7,
    start: date = date(2025, 11, 1),
    months: int = 12,
    dirty_rate: float = 0.001,
    partitions: int | None = None,
):
    """Fictional statements for `n_customers`, generated in parallel on the cluster (no data on the driver)."""
    ids = spark.range(n_customers).selectExpr("format_string('C%06d', id) AS customer_id")
    if partitions:
        ids = ids.repartition(partitions)

    def gen(batches):
        for b in batches:
            yield _bronze_rows(b["customer_id"].tolist(), seed, start, months, dirty_rate)

    return ids.mapInPandas(gen, BRONZE_SCHEMA)


# --- silver ------------------------------------------------------------------------------------------------


def _parse(pdf: pd.DataFrame) -> pd.DataFrame:
    from .categorize import categorise, normalise_merchant
    from .importers import parse_amount, parse_date

    dates, cents, reasons, merchants, cats, rules = [], [], [], [], [], []
    for d_raw, a_raw, label in zip(pdf["date_raw"], pdf["amount_raw"], pdf["label"], strict=True):
        try:
            d, c = parse_date(d_raw or ""), parse_amount(a_raw or "")
            if c is None:
                raise ValueError("empty amount")
            reason = None
        except ValueError as e:
            d, c, reason = None, None, str(e)
        cat, rule = categorise(label, c or 0) if reason is None else (None, None)
        dates.append(d)
        cents.append(c)
        reasons.append(reason)
        merchants.append(normalise_merchant(label))
        cats.append(cat)
        rules.append(rule)
    out = pdf.copy()
    out["date"], out["amount_cents"], out["reason"] = dates, cents, reasons
    out["merchant"], out["category"], out["category_rule"] = merchants, cats, rules
    return out


PARSED_SCHEMA = (
    BRONZE_SCHEMA + ", date date, amount_cents long, reason string, merchant string, category string, "
    "category_rule string"
)


def build_silver(bronze):
    """Returns (silver, quarantine). Ids are the same as the Python importer's: same statement, same ids."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    parsed = bronze.mapInPandas(lambda batches: (_parse(b) for b in batches), PARSED_SCHEMA)
    quarantine = parsed.where("reason IS NOT NULL").select(*[c.split()[0] for c in QUARANTINE_SCHEMA.split(", ")])
    ok = parsed.where("reason IS NULL")
    occurrence = F.row_number().over(
        Window.partitionBy("customer_id", "date", "amount_cents", "label").orderBy("line_no")
    )
    key = F.concat_ws(
        "|",
        "customer_id",
        F.date_format("date", "yyyy-MM-dd"),
        F.col("amount_cents").cast("string"),
        "label",
        occurrence.cast("string"),
    )
    silver = (
        ok.withColumn("transaction_id", F.substring(F.sha2(key, 256), 1, 16))
        .select(*[c.split()[0] for c in SILVER_SCHEMA.split(", ")])
        .dropDuplicates(["customer_id", "transaction_id"])
    )
    return silver, quarantine


# --- gold --------------------------------------------------------------------------------------------------


def gold_monthly_cashflow(silver):
    from pyspark.sql import functions as F

    month = F.date_format("date", "yyyy-MM").alias("month")
    is_income, is_saving = F.col("category") == "Revenus", F.col("category") == "Epargne"
    return silver.groupBy("customer_id", month).agg(
        (F.sum(F.when(is_income, F.col("amount_cents")).otherwise(0)) / 100).alias("income"),
        (-F.sum(F.when(~is_income & ~is_saving, F.col("amount_cents")).otherwise(0)) / 100).alias("spending"),
        (-F.sum(F.when(is_saving, F.col("amount_cents")).otherwise(0)) / 100).alias("saved"),
        (F.sum(F.when(~is_saving, F.col("amount_cents")).otherwise(0)) / 100).alias("net"),
        F.count("*").alias("transactions"),
    )


def gold_spending_by_category(silver):
    from pyspark.sql import functions as F

    return (
        silver.where("category NOT IN ('Revenus', 'Epargne')")
        .groupBy("customer_id", F.date_format("date", "yyyy-MM").alias("month"), "category")
        .agg((-F.sum("amount_cents") / 100).alias("spent"), F.count("*").alias("transactions"))
        .where("spent > 0")
    )


def _transactions(pdf: pd.DataFrame):
    from .schemas import Transaction

    return [
        Transaction(
            id=r.transaction_id,
            customer_id=r.customer_id,
            date=r.date,
            amount_cents=int(r.amount_cents),
            label=r.label,
            merchant=r.merchant,
            category=r.category,
            category_rule=r.category_rule,
        )
        for r in pdf.sort_values(["date", "transaction_id"]).itertuples()
    ]


def _subscriptions(pdf: pd.DataFrame) -> pd.DataFrame:
    from .subscriptions import detect

    cols = [c.split()[0] for c in SUBSCRIPTIONS_SCHEMA.split(", ")]
    subs = detect(_transactions(pdf))
    return pd.DataFrame(
        [
            (
                pdf["customer_id"].iloc[0],
                s.merchant,
                s.category,
                s.cadence,
                s.amount,
                s.previous_amount,
                s.annual_cost,
                s.occurrences,
                s.first_date,
                s.last_date,
                s.next_expected,
                s.active,
            )
            for s in subs
        ],
        columns=cols,
    )


def gold_subscriptions(silver):
    return silver.groupBy("customer_id").applyInPandas(_subscriptions, SUBSCRIPTIONS_SCHEMA)


def _capacity(pdf: pd.DataFrame) -> pd.DataFrame:
    from .analytics import complete_months, savings_capacity

    txs = _transactions(pdf)
    fig = savings_capacity(txs)
    return pd.DataFrame(
        [(pdf["customer_id"].iloc[0], fig.value, len(complete_months(txs)[-6:]), fig.formula)],
        columns=["customer_id", "savings_capacity", "months_used", "formula"],
    )


def gold_savings_capacity(silver):
    return silver.groupBy("customer_id").applyInPandas(_capacity, CAPACITY_SCHEMA)


def sql_statements(script: str) -> list[str]:
    """Split a SQL script on the semicolons that end statements (not those inside quoted strings), drop comments."""
    statements, buf, quote = [], [], None
    for line in script.splitlines():
        if not quote and line.strip().startswith("--"):
            continue
        for ch in line + "\n":
            if quote:
                quote = None if ch == quote else quote
            elif ch in "'\"":
                quote = ch
            elif ch == ";":
                statements.append("".join(buf).strip())
                buf = []
                continue
            buf.append(ch)
    tail = "".join(buf).strip()
    return [s for s in [*statements, tail] if s]


def render_sql(script: str, catalog: str, schema: str) -> str:
    """Fill {catalog} and {schema} (str.format would break on the regex quantifiers such as {2,7})."""
    return script.replace("{catalog}", catalog).replace("{schema}", schema)


def governance_sql(catalog: str, schema: str) -> list[str]:
    """The Unity Catalog governance statements, shipped inside the package (no workspace file to read at run time)."""
    from importlib import resources

    script = resources.files("nestegg").joinpath("data/governance.sql").read_text(encoding="utf-8")
    return sql_statements(render_sql(script, catalog, schema))


# An IBAN (FR76 3000 ...) or an e-mail address still visible. Plain string, no formatting: the regex
# quantifiers {2} and {2,7} must reach Spark exactly as written.
LEAK_PATTERNS = ("[A-Z]{2}[0-9]{2}( ?[A-Z0-9]{4}){2,7}", "@")


def leak_count_sql(table: str, columns: tuple[str, ...] = ("label", "merchant")) -> str:
    """Counts the rows of `table` where a column still shows an IBAN or an e-mail (the job's final check: 0)."""
    where = " OR ".join(f"{c} RLIKE '{p}'" for c in columns for p in LEAK_PATTERNS)
    return f"SELECT count(*) AS n FROM {table} WHERE " + where
