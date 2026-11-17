-- Nestegg governance in Unity Catalog. Placeholders {catalog} and {schema} are filled by the job.
--
-- Design: the pipeline's base tables are private to their owner. Consumers (the assistant, analysts) only get
-- the secured views below, which apply two rules inside the platform, whatever tool queries them:
--   1. row-level security (dynamic views): a user sees only the customers mapped to them in access_map;
--   2. masking: IBANs, card numbers, e-mails and names in transfer labels are replaced before anyone reads them.

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.access_map (
  user_email STRING NOT NULL COMMENT 'Workspace user (current_user())',
  customer_id STRING NOT NULL COMMENT 'Customer this user may see'
) COMMENT 'Who may see which customer: the single source of truth for row-level security';

CREATE OR REPLACE FUNCTION {catalog}.{schema}.mask_label(label STRING)
RETURNS STRING
COMMENT 'Masks personal data in a bank label: IBAN, card number, e-mail, names of transfer counterparties'
RETURN regexp_replace(
  regexp_replace(
    regexp_replace(
      regexp_replace(label,
        '\\b[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\\b', '[IBAN]'),
      '\\b[0-9](?:[ -]?[0-9]){12,18}\\b', '[CARD]'),
    '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', '[EMAIL]'),
  '\\b((?:VIR(?:EMENT)?|PAYLIB|LYDIA|WERO)(?: SEPA)?(?: INST)?(?: EMIS| RECU)?(?: A| DE| VERS| POUR| /DE:| /A:)?(?: M\\.| MME| MR)?) (?!LIVRET|EPARGNE|SALAIRE|LOYER|LDDS|LEP)[A-Z][A-Z-]+(?: (?!REMBOURSEMENT|LOYER|SALAIRE|/MOTIF)[A-Z][A-Z-]+){1,3}',
  '$1 [NAME]');

CREATE OR REPLACE VIEW {catalog}.{schema}.customer_transactions
COMMENT 'Transactions as the assistant may read them: own customers only, personal data masked'
AS SELECT transaction_id, customer_id, date, amount_cents, {catalog}.{schema}.mask_label(label) AS label,
          merchant, category, category_rule
   FROM {catalog}.{schema}.silver_transactions
   WHERE customer_id IN (SELECT m.customer_id FROM {catalog}.{schema}.access_map m WHERE m.user_email = current_user());

CREATE OR REPLACE VIEW {catalog}.{schema}.customer_cashflow
COMMENT 'Monthly cashflow, own customers only'
AS SELECT * FROM {catalog}.{schema}.gold_monthly_cashflow WHERE customer_id IN (SELECT m.customer_id FROM {catalog}.{schema}.access_map m WHERE m.user_email = current_user());

CREATE OR REPLACE VIEW {catalog}.{schema}.customer_subscriptions
COMMENT 'Detected subscriptions, own customers only'
AS SELECT * FROM {catalog}.{schema}.gold_subscriptions WHERE customer_id IN (SELECT m.customer_id FROM {catalog}.{schema}.access_map m WHERE m.user_email = current_user());

CREATE OR REPLACE VIEW {catalog}.{schema}.customer_savings_capacity
COMMENT 'Savings capacity, own customers only'
AS SELECT * FROM {catalog}.{schema}.gold_savings_capacity WHERE customer_id IN (SELECT m.customer_id FROM {catalog}.{schema}.access_map m WHERE m.user_email = current_user());
