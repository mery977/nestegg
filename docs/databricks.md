# Nestegg on Databricks

The same rules as the Python package, at bank scale, with governance enforced by the platform.

```
fictional statements (2,000+ customers, generated on the cluster)
  -> bronze_statement_lines        raw lines as banks export them (text dates, French amounts)
  -> silver_transactions           parsed, typed, deduplicated, merchant + explained category
     quarantine_statement_lines    unreadable lines, with the reason (never dropped silently)
  -> gold_monthly_cashflow, gold_spending_by_category, gold_subscriptions, gold_savings_capacity
  -> secured views (Unity Catalog)  customer_transactions, customer_cashflow, customer_subscriptions,
                                    customer_savings_capacity: own customers only, personal data masked
```

## Why it is built this way

- **One business logic, two runtimes.** Parsing, categories, subscriptions and savings capacity are the package's own
  functions, run inside pandas UDFs. `tests/test_lakehouse.py` checks that Spark and the plain Python path give the
  same transaction ids and the same figures, to the cent.
- **Governance in the platform, not in the application.** Base tables are private to the pipeline. Consumers (the
  assistant, analysts, BI) read secured views: row-level security from an `access_map` table (`current_user()`), and
  `mask_label()` (in `src/nestegg/data/governance.sql`) masking IBANs, card numbers, e-mails and transfer counterparties. Whatever tool queries the data,
  the rules apply. The masking SQL is tested on Spark against the same cases as the Python vault.
- **Quality you can audit.** Unreadable lines go to quarantine with their reason; the `checks` task fails the run if
  row-level security or masking is broken.
- **Serverless, one command.** A Databricks Asset Bundle (`databricks.yml`) deploys the job; it runs on Free Edition
  (catalog `workspace`) and on Azure Databricks (`--var catalog=<your catalog>`).

## Run it (Windows)

1. Create a free workspace: <https://www.databricks.com/learn/free-edition>. Copy its address from the browser
   (`https://dbc-xxxxxxxx-xxxx.cloud.databricks.com`).
2. From the project folder:
   ```powershell
   .\scripts\databricks.ps1 -Workspace https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
   ```
   It installs the Databricks CLI if needed, opens the browser to log in, validates, deploys and runs the job.
3. In Databricks: **Catalog > workspace > nestegg** for the tables and their comments, and
   `databricks/sql/demo_queries.sql` in the SQL editor to see governance at work.

macOS / Linux: `databricks auth login --host <workspace>`, then `databricks bundle deploy && databricks bundle run nestegg_lakehouse`.

## Free Edition limits that shape the design

Serverless compute only, no GPU, one vector search endpoint, fair-usage quotas, no commercial use. The pipeline is
sized for it (2,000 customers by default, `--var customers=` to change). GPU work (fine-tuning) runs elsewhere and is
registered in MLflow on Databricks.
