"""Databricks job entry point (serverless). One stage per task: bronze -> silver -> gold -> governance -> checks.

The nestegg package is installed from the wheel the bundle builds (environment dependency), on the driver and on
every worker, together with the governance SQL: nothing is read from workspace files at run time.
"""

import argparse

from pyspark.sql import SparkSession

from nestegg import lakehouse as lh

parser = argparse.ArgumentParser()
parser.add_argument("--stage", required=True, choices=["bronze", "silver", "gold", "governance", "checks"])
parser.add_argument("--root", default="", help="synced bundle folder (unused, kept for compatibility)")
parser.add_argument("--catalog", default="workspace")
parser.add_argument("--schema", default="nestegg")
parser.add_argument("--customers", type=int, default=2000)
parser.add_argument("--demo-customers", default="C000001,C000002", help="customers mapped to the job's user")
args = parser.parse_args()


spark = SparkSession.builder.getOrCreate()
ns = f"{args.catalog}.{args.schema}"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ns} COMMENT 'Nestegg: fictional bank statements and savings analytics'")


def save(df, name: str, comment: str) -> None:
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ns}.{name}")
    spark.sql(f"COMMENT ON TABLE {ns}.{name} IS '{comment}'")
    print(f"{ns}.{name}: {spark.table(f'{ns}.{name}').count()} rows")


if args.stage == "bronze":
    save(
        lh.generate_bronze(spark, args.customers),
        "bronze_statement_lines",
        "Raw statement lines as exported by banks (fictional customers)",
    )
elif args.stage == "silver":
    silver, quarantine = lh.build_silver(spark.table(f"{ns}.bronze_statement_lines"))
    save(silver, "silver_transactions", "Parsed, typed, deduplicated transactions with merchant and explained category")
    save(quarantine, "quarantine_statement_lines", "Lines that could not be read, with the reason")
elif args.stage == "gold":
    silver = spark.table(f"{ns}.silver_transactions")
    save(lh.gold_monthly_cashflow(silver), "gold_monthly_cashflow", "Income, spending, savings and net per month")
    save(lh.gold_spending_by_category(silver), "gold_spending_by_category", "Spending per customer, month and category")
    save(lh.gold_subscriptions(silver), "gold_subscriptions", "Recurring payments, price changes, stopped ones")
    save(lh.gold_savings_capacity(silver), "gold_savings_capacity", "Median monthly savings capacity")
elif args.stage == "governance":
    for statement in lh.governance_sql(args.catalog, args.schema):
        spark.sql(statement)
    me = spark.sql("SELECT current_user() AS u").first().u
    rows = [(me, c.strip()) for c in args.demo_customers.split(",") if c.strip()]
    spark.createDataFrame(rows, "user_email string, customer_id string").createOrReplaceTempView("mine")
    spark.sql(
        f"MERGE INTO {ns}.access_map t USING mine s ON t.user_email = s.user_email AND t.customer_id = s.customer_id "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    print(f"secured views created; {me} may see {args.demo_customers}")
elif args.stage == "checks":
    visible = {
        r.customer_id for r in spark.sql(f"SELECT DISTINCT customer_id FROM {ns}.customer_transactions").collect()
    }
    mapped = {
        r.customer_id
        for r in spark.sql(f"SELECT customer_id FROM {ns}.access_map WHERE user_email = current_user()").collect()
    }
    assert visible == mapped, f"row-level security broken: visible {visible} vs mapped {mapped}"
    leaks = spark.sql(lh.leak_count_sql(f"{ns}.customer_transactions")).first().n
    assert leaks == 0, f"{leaks} labels still show an IBAN or an e-mail"
    total = spark.table(f"{ns}.silver_transactions").count()
    quarantined = spark.table(f"{ns}.quarantine_statement_lines").count()
    print(
        f"OK: row-level security ({len(visible)} visible customers), masking (0 leaks), "
        f"{total} transactions, {quarantined} quarantined lines"
    )
