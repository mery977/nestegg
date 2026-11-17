# Nestegg

**The savings assistant a bank could put in its app: every figure is computed by code and traceable to
the transactions behind it, personal data is masked before any model sees it, and the guarantees are
proven by tests.**

A customer imports bank statements and asks: *where does my money go, what can I save, how do I reach my
goal?* A general chatbot can answer that once, from a file. A bank cannot use one: customer data must stay
in its infrastructure, every number shown to a client must be exact and auditable, and regulated
investment advice must be refused. Nestegg is built around those constraints.

> Status: the assistant (agent + guardrails + chat) and its Databricks lakehouse with Unity Catalog governance.
> Next: the agent on Databricks with MLflow tracing, automated red teaming with LLM judges, fine-tuning, Azure deployment.

## What works today

| | |
|---|---|
| **Import any French bank export** | CSV with `;` or `,`, decimal comma or dot, signed amounts or debit/credit columns, header lines above the table, UTF-8 or Windows encodings. Rows that cannot be read are reported with their line number, never dropped silently. |
| **Explainable categories** | Merchants are extracted from noisy labels (`CB NETFLIX.COM 12/10 X4974` → `NETFLIX.COM`) and categorised by rules; each category states the rule that produced it. Unknown merchants stay *to categorise* instead of being guessed. |
| **Subscriptions** | Weekly, monthly and yearly series, price increases (*Netflix 13.49 → 15.99*), and cancelled subscriptions reported as stopped rather than counted as costs. |
| **Proven figures** | Spending per category, monthly cashflow, savings capacity (median of complete months), spending drifts, bank fees. Each figure carries its formula and the ids of its transactions; a test recomputes every figure from them. |
| **Regulated savings, computed like a bank** | Livret A, LDDS, LEP with the fortnight rule (interest from the 1st or 16th following a deposit, capitalised on 31 December) and the ceiling. Rates live in a dated, sourced file. Simulations state that rates are revised twice a year. |
| **Goals** | Monthly effort to reach a target by a date, compared with the customer's real savings capacity. |

## The assistant

```
question ──► input guardrails ──► agent loop ─────────────────────────► output guardrails ──► answer + proof
             advice? other        model asks for a tool ──► tool runs     every number from a
             customer? injection?  on this customer only  ◄── figures     tool? no product
             → refused, no model   (masked) ─► model ... ─► answer         recommendation?
                                   no personal value may reach the model   → corrected once, else
                                                                             verified figures only
                                                         every turn ──► audit log (no personal data)
```

- **Every number is computed by code.** The model chooses tools and writes sentences; it never adds, averages
  or projects. An answer containing a number no tool returned is sent back once, then replaced by the verified
  figures: an invented figure never reaches the customer (tested with a model that keeps inventing).
- **Regulated advice is refused**, before any model call, with the reason and what the assistant can do
  instead (budget analysis, Livret A / LDDS / LEP simulation, pointer to a registered adviser).
- **Each answer carries its proof**: the figures cited, their formulas, and the transactions behind them.
- **Any model, same guarantees**: offline planner (no key), Azure OpenAI (keyless with managed identity),
  OpenAI, Claude, Gemini, or a local open model through an OpenAI-compatible server (e.g. Ollama: nothing
  leaves the machine). With a free Gemini API key, real customer data is blocked: the free tier may use
  prompts to improve Google's products.

```powershell
.\scripts\ui.ps1                       # chat interface, offline (no key)
.\scripts\ui.ps1 -Provider anthropic   # with Claude (or openai, azure, gemini, local)
```
```text
nestegg ask  --customer C001 "Quels sont mes abonnements ?" [--provider anthropic]
nestegg chat --customer C001
```

## On Databricks: the same rules at bank scale

```
fictional statements, 2,000+ customers  ->  bronze  ->  silver (+ quarantine)  ->  gold  ->  Unity Catalog secured views
```

- **Same figures on a laptop and on Spark**: the package's functions run inside pandas UDFs, and a test checks that both
  paths give the same transaction ids and the same amounts, to the cent.
- **Governance enforced by the platform**: secured views apply row-level security (`access_map`, `current_user()`)
  and mask IBANs, cards, e-mails and names with a SQL function tested on Spark; the job fails if either breaks.
- **One command**: a Databricks Asset Bundle deploys the serverless job, on Free Edition or Azure Databricks.

```powershell
.\scripts\databricks.ps1 -Workspace https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
```

Details: [docs/databricks.md](docs/databricks.md).

## Security, designed in from the first line

| Guarantee | How | Proven by |
|---|---|---|
| A language model never sees personal data | IBANs (checksum-validated), card numbers (Luhn-validated), e-mails, phone numbers and names in transfer labels are replaced by tokens (`[IBAN_1]`, `[PERSON_2]`). The mapping stays server-side, per customer. | `test_security.py`: no friend name or IBAN survives on 900+ demo transactions |
| A customer only ever sees their own data | Isolation is enforced by the storage layer: an agent receives a view bound to one customer, with no method that accepts another customer id. | Cross-customer read and write attempts fail |
| Right to erasure (GDPR art. 17) | `nestegg forget` deletes every row of one customer. | Other customers untouched, nothing left |
| Investment advice is refused | Deterministic input check (French and English) before any model call; output check on the answer. | `test_agent.py`: 8 advice phrasings refused, 8 legitimate questions allowed |
| No invented figure reaches a customer | Every number of an answer must match a tool result; one correction round, then verified figures only. | Scripted models that invent, compute or recommend |
| Bank labels cannot steer the agent | Tool results are data with a note; the agent has no tool that writes, pays or sends. | A label carrying "IGNORE PREVIOUS INSTRUCTIONS" changes nothing |
| Fail closed on leaks | A prompt containing a card number, IBAN, e-mail or phone number is not sent. | `test_a_leak_in_the_prompt_blocks_the_call` |
| Audit without personal data | One JSON line per turn: pseudonymous customer, model, data location, tools, figures, guardrail events. A line containing personal data is refused. | `test_audit_log_has_no_personal_data` |
| No real data in the repository | Fictional statements are generated (invented names, IBANs on a non-existent bank code). Real statements and databases are git-ignored. | `.gitignore` |

What this project is not: a certified banking system. A bank would add external audits, encryption at rest
managed by its own key service, and frameworks such as PCI DSS or DORA. The design leaves room for them.

## Try it

```powershell
.\scripts\setup.ps1            # Windows: environment, tests, demo customers, first report
```
```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
pytest -q && nestegg demo && nestegg report --customer C001
```

```text
nestegg report   --customer C001                          # spending, subscriptions, drifts, capacity
nestegg plan     --customer C001 --target 5000 --by 2027-12
nestegg simulate --product livret_a --monthly 200 --months 24
nestegg mask     --customer C001                          # what a language model would see
nestegg import   my-statement.csv --customer ME           # your own export: stays on your machine
nestegg forget   --customer ME
```

## Layout

```
src/nestegg/
  importers.py      bank CSV detection and parsing (amounts in integer cents)
  categorize.py     merchant extraction, explainable rule-based categories
  subscriptions.py  recurring payments, price changes, stopped series
  analytics.py      figures with formula and transaction ids
  planner.py        goals, Livret A / LDDS / LEP simulation (fortnight rule)
  pii.py            masking vault per customer
  store.py          SQLite storage with customer isolation and erasure
  demo.py           realistic fictional statements + ground truth
  tools.py          the agent's read-only tools, each number returned as a referenced figure
  agent.py          tool loop, leak guard, output verification, audit
  guardrails.py     advice / other-customer / injection checks, number verification
  llm.py            providers: offline, Azure OpenAI, OpenAI, Claude, Gemini, local (Ollama)
  offline.py        deterministic planner: the agent runs without any model or key
  audit.py          JSONL audit trail without personal data
  lakehouse.py      Spark pipeline: bronze / silver / quarantine / gold, same functions as the package
databricks/         job entry point, Unity Catalog governance SQL, demo queries (databricks.yml at the root)
ui/app.py           chat + dashboard (Streamlit)
  data/rates.json   regulated rates, dated and sourced
tests/              import, security, analytics, agent, providers, CLI, lakehouse (Spark)
```

## License

MIT. Author: Meryem El Alaoui Hassani.
