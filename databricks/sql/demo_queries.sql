-- Run in the Databricks SQL editor after the job (catalog "workspace", schema "nestegg").

-- 1. Scale: fictional bank statements, parsed and categorised
SELECT count(*) AS transactions, count(DISTINCT customer_id) AS customers FROM workspace.nestegg.silver_transactions;

-- 2. Data quality: lines that could not be read, and why
SELECT reason, count(*) AS lines FROM workspace.nestegg.quarantine_statement_lines GROUP BY reason ORDER BY lines DESC;

-- 3. Governance: through the secured view you only see your mapped customers, with personal data masked
SELECT customer_id, date, label, amount_cents / 100 AS amount
FROM workspace.nestegg.customer_transactions
WHERE label LIKE 'VIR%' ORDER BY date DESC LIMIT 20;

-- 4. ... while the base table (private to its owner) holds every customer
SELECT count(DISTINCT customer_id) AS in_base_table FROM workspace.nestegg.silver_transactions;

-- 5. Gold: subscriptions that went up in price, across all customers
SELECT merchant, count(*) AS customers, round(avg(amount - previous_amount), 2) AS avg_increase
FROM workspace.nestegg.gold_subscriptions
WHERE previous_amount IS NOT NULL AND amount > previous_amount
GROUP BY merchant ORDER BY customers DESC;

-- 6. Gold: distribution of monthly savings capacity
SELECT width_bucket(savings_capacity, -1000, 4000, 10) AS bucket, min(savings_capacity) AS from_eur,
       max(savings_capacity) AS to_eur, count(*) AS customers
FROM workspace.nestegg.gold_savings_capacity GROUP BY bucket ORDER BY bucket;
