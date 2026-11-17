"""The lakehouse pipeline gives exactly the same results as the Python package: same ids, same figures.
Runs on a local Spark (skipped when PySpark is not installed: pip install -e ".[spark]")."""

import os
import sys

import pytest

pyspark = pytest.importorskip("pyspark")


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    s = (
        SparkSession.builder.master("local[2]")
        .appName("nestegg-tests")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


@pytest.fixture(scope="module")
def tables(spark):
    from nestegg import lakehouse as lh

    bronze = lh.generate_bronze(spark, 12, dirty_rate=0.0).cache()
    silver, quarantine = lh.build_silver(bronze)
    return bronze, silver.cache(), quarantine


def _python_side(customer_id):
    """The same customer through the plain Python path: generator -> CSV text -> importer -> analytics."""
    from nestegg.categorize import enrich
    from nestegg.demo import generate, random_persona
    from nestegg.importers import read_csv

    rows, truth = generate(random_persona(customer_id))
    csv = "Date;Libellé;Montant\n" + "".join(
        f"{d:%d/%m/%Y};{label};{f'{a:.2f}'.replace('.', ',')}\n" for d, a, label in rows
    )
    txs, _ = read_csv(csv, customer_id)
    return enrich(txs), truth


def test_silver_ids_and_categories_match_the_python_importer(tables):
    _, silver, quarantine = tables
    assert quarantine.count() == 0
    txs, _ = _python_side("C000003")
    spark_rows = {r.transaction_id: r for r in silver.where("customer_id = 'C000003'").collect()}
    assert set(spark_rows) == {t.id for t in txs}
    for t in txs:
        r = spark_rows[t.id]
        assert (r.amount_cents, r.date, r.merchant, r.category) == (t.amount_cents, t.date, t.merchant, t.category)


def test_gold_figures_match_the_python_figures(tables):
    from nestegg import analytics, subscriptions
    from nestegg import lakehouse as lh

    _, silver, _ = tables
    txs, truth = _python_side("C000007")
    gold_flow = {r.month: r for r in lh.gold_monthly_cashflow(silver).where("customer_id = 'C000007'").collect()}
    for f in analytics.monthly_cashflow(txs):
        g = gold_flow[f["month"]]
        assert (round(g.income, 2), round(g.spending, 2), round(g.saved, 2), round(g.net, 2)) == (
            f["income"].value,
            f["spending"].value,
            f["saved"].value,
            f["net"].value,
        )
    subs = {r.merchant: r for r in lh.gold_subscriptions(silver).where("customer_id = 'C000007'").collect()}
    assert set(subs) == set(truth["subscriptions"]) == {s.merchant for s in subscriptions.detect(txs)}
    cap = lh.gold_savings_capacity(silver).where("customer_id = 'C000007'").collect()[0]
    assert cap.savings_capacity == analytics.savings_capacity(txs).value


def test_spending_by_category_never_counts_income_or_savings(tables):
    from nestegg import lakehouse as lh

    _, silver, _ = tables
    cats = {r.category for r in lh.gold_spending_by_category(silver).collect()}
    assert cats and not cats & {"Revenus", "Epargne"}


def test_unreadable_lines_are_quarantined_with_their_reason(spark):
    from nestegg import lakehouse as lh

    bronze = lh.generate_bronze(spark, 20, dirty_rate=0.02)
    silver, quarantine = lh.build_silver(bronze)
    total, ok, bad = bronze.count(), silver.count(), quarantine.collect()
    assert bad and ok + len(bad) == total
    assert all(r.reason.startswith(("not a date", "not an amount")) for r in bad)


def test_unity_catalog_masking_function_masks_like_the_python_vault(spark):
    """The SQL mask_label used by the secured views, run on Spark: same personal data masked as pii.redact."""
    from pathlib import Path

    from nestegg.pii import find_leaks

    sql = (Path(__file__).parent.parent / "src" / "nestegg" / "data" / "governance.sql").read_text(encoding="utf-8")
    start = sql.index("RETURN regexp_replace(", sql.index("mask_label")) + len("RETURN ")
    expr = sql[start : sql.index(";", start)]
    cases = {
        "VIR SEPA EMIS A LEA MOREAU FR97 9999 9793 5463 2682 0426 082": "VIR SEPA EMIS A [NAME] [IBAN]",
        "VIR INST RECU DE M. YASMINE MARTIN REMBOURSEMENT": "VIR INST RECU DE M. [NAME] REMBOURSEMENT",
        "VIR SEPA RECU /DE: NOVALYS CONSEIL /MOTIF SALAIRE 112025": "VIR SEPA RECU /DE: [NAME] /MOTIF SALAIRE 112025",
        "PAYPAL *CONTACT.LOCATION@EXEMPLE.FR": "PAYPAL *[EMAIL]",
        "CARTE 4111 1111 1111 1111 BOUTIQUE": "CARTE [CARD] BOUTIQUE",
        "VIR LIVRET A EPARGNE": "VIR LIVRET A EPARGNE",
        "CB NETFLIX.COM 12/10 X4974": "CB NETFLIX.COM 12/10 X4974",
    }
    spark.createDataFrame([(k,) for k in cases], "label string").createOrReplaceTempView("labels")
    got = {r.label: r.masked for r in spark.sql(f"SELECT label, {expr} AS masked FROM labels").collect()}
    assert got == cases and not any(find_leaks(v) for v in got.values())


def test_governance_script_splits_into_its_statements():
    from pathlib import Path

    from nestegg.lakehouse import render_sql, sql_statements

    sql = (Path(__file__).parent.parent / "src" / "nestegg" / "data" / "governance.sql").read_text(encoding="utf-8")
    stmts = sql_statements(render_sql(sql, "workspace", "nestegg"))
    assert len(stmts) == 6 and all(not s.startswith("--") for s in stmts)
    assert stmts[1].startswith("CREATE OR REPLACE FUNCTION workspace.nestegg.mask_label") and "'[IBAN]'" in stmts[1]


def test_governance_sql_ships_inside_the_package():
    from nestegg.lakehouse import governance_sql

    stmts = governance_sql("workspace", "nestegg")
    assert len(stmts) == 6 and stmts[-1].startswith(
        "CREATE OR REPLACE VIEW workspace.nestegg.customer_savings_capacity"
    )


def test_leak_check_runs_on_spark_and_finds_only_unmasked_labels(spark, tables):
    """The job's final check, run as-is: 0 leaks on masked labels, every IBAN and e-mail found on raw ones."""
    from pathlib import Path

    from nestegg.lakehouse import leak_count_sql

    _, silver, _ = tables
    sql = (Path(__file__).parent.parent / "src" / "nestegg" / "data" / "governance.sql").read_text(encoding="utf-8")
    start = sql.index("RETURN regexp_replace(", sql.index("mask_label")) + len("RETURN ")
    expr = sql[start : sql.index(";", start)]
    silver.createOrReplaceTempView("raw_labels")
    spark.sql(f"SELECT {expr} AS label FROM raw_labels").createOrReplaceTempView("masked_labels")
    assert spark.sql(leak_count_sql("masked_labels")).first().n == 0
    raw_leaks = spark.sql(leak_count_sql("raw_labels")).first().n
    expected = silver.where("label LIKE 'VIR SEPA EMIS A %' OR label LIKE '%@%'").count()
    assert raw_leaks == expected > 0
