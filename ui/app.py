"""Nestegg chat. Run from the project folder:   streamlit run ui/app.py

The assistant runs in this process, on the local database (nestegg.db). Choose a fictional customer and a
model in the sidebar. Under every answer: the proof of each figure, the guardrails that acted, and exactly
what the model saw (masked).
"""

from __future__ import annotations

import os

import streamlit as st

from nestegg import analytics, subscriptions
from nestegg.agent import Assistant, LeakBlocked, PolicyError
from nestegg.cli import ensure_demo
from nestegg.importers import read_csv
from nestegg.llm import DATA_LOCATION, DEFAULT_MODELS, make_llm
from nestegg.store import Store

st.set_page_config(page_title="Nestegg", page_icon="🪺", layout="wide")
st.markdown(
    """
<style>
.block-container {padding-top: 1.6rem; max-width: 1150px;}
.tag {display:inline-block; padding:2px 9px; border-radius:999px; font-size:0.78rem; font-weight:600; margin-right:6px;}
.ok {background:#e6f4ea; color:#137333;}
.warn {background:#fef7e0; color:#8a5a00;}
.stop {background:#fce8e6; color:#c5221f;}
@media (prefers-color-scheme: dark) {
  .ok {background:#1e3a2a; color:#81c995;}
  .warn {background:#3a3112; color:#fdd663;}
  .stop {background:#3c1f1d; color:#f28b82;}
}
</style>
""",
    unsafe_allow_html=True,
)

DB = os.environ.get("NESTEGG_DB", "nestegg.db")


@st.cache_resource
def get_store() -> Store:
    ensure_demo(DB)
    return Store(DB)


store = get_store()
available = ["offline"] + [
    p
    for p, env in [
        ("azure", "NESTEGG_AZURE_ENDPOINT"),
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("local", "NESTEGG_LOCAL_URL"),
    ]
    if os.environ.get(env)
]

DEMO = ["C001", "C002", "C003"]

with st.sidebar:
    st.header("Nestegg")

    with st.expander("Import my bank statement", expanded=not [c for c in store.customers() if c not in DEMO]):
        st.caption(
            "Export your account history as CSV from your bank's website (usually *Comptes > Télécharger / "
            "Exporter*), then drop it here. Any French bank layout works. The file is read on this machine and "
            "stored in the local database only."
        )
        owner = st.text_input("Name of this profile", value="MOI", max_chars=20).strip().upper() or "MOI"
        uploaded = st.file_uploader("Statement (CSV)", type=["csv", "txt"], accept_multiple_files=True)
        if uploaded and st.button("Import", type="primary"):
            if owner in DEMO:
                st.error("Choose another name: C001 to C003 are the fictional customers.")
            else:
                total = 0
                for f in uploaded:
                    try:
                        txs, report = read_csv(f.getvalue(), owner)
                        added = store.for_customer(owner).add(txs)
                        total += added
                        st.success(f"{f.name}: {report.imported} transactions read, {added} new.")
                        for line, reason in report.skipped[:5]:
                            st.warning(f"line {line} skipped: {reason}")
                    except ValueError as e:
                        st.error(f"{f.name}: {e}")
                if total:
                    st.session_state.select_customer = owner
                    st.rerun()

    real = [c for c in store.customers() if c not in DEMO]
    options = real + DEMO
    chosen = st.session_state.pop("select_customer", None)
    customer = st.selectbox(
        "Profile",
        options,
        index=options.index(chosen) if chosen in options else 0,
        format_func=lambda c: f"{c} (fictional)" if c in DEMO else f"{c} (your data)",
    )
    wanted = os.environ.get("NESTEGG_PROVIDER", "offline")
    provider = st.selectbox(
        "Model",
        available,
        index=available.index(wanted) if wanted in available else 0,
        help="Set an API key in the environment to see more providers.",
    )
    st.caption(f"Model: `{DEFAULT_MODELS.get(provider, 'rules')}` · data sent to: {DATA_LOCATION[provider]}")
    show_view = st.toggle("Show what the model saw", value=False)
    if customer not in DEMO:
        st.divider()
        if st.button(
            f"Erase all data of {customer}", help="GDPR article 17: deletes every transaction of this profile."
        ):
            n = store.for_customer(customer).erase()
            st.session_state.pop("key", None)
            st.success(f"{n} transactions deleted.")
            st.rerun()
    st.divider()
    st.caption("Read-only assistant: it cannot move money or change anything. Every figure is computed by code.")

key = f"{customer}-{provider}"
if st.session_state.get("key") != key:
    try:
        st.session_state.assistant = Assistant(store.for_customer(customer), make_llm(provider))
        st.session_state.messages = []
        st.session_state.key = key
    except (PolicyError, KeyError, ImportError, ValueError) as e:
        st.error(str(e))
        st.stop()
assistant: Assistant = st.session_state.assistant

tab_chat, tab_board = st.tabs(["Assistant", "Dashboard"])

with tab_board:
    txs = store.for_customer(customer).transactions()
    cap = analytics.savings_capacity(txs)
    subs = subscriptions.detect(txs)
    c1, c2, c3 = st.columns(3)
    c1.metric("Savings capacity / month", f"{cap.value:,.2f} €".replace(",", " "))
    c2.metric("Active subscriptions / year", f"{sum(s.annual_cost for s in subs if s.active):,.2f} €".replace(",", " "))
    c3.metric("Bank fees (period)", f"{analytics.bank_fees(txs).value:,.2f} €".replace(",", " "))
    flows = analytics.monthly_cashflow(txs)
    st.subheader("Income and spending per month")
    st.bar_chart(
        {
            "income": {f["month"]: f["income"].value for f in flows},
            "spending": {f["month"]: f["spending"].value for f in flows},
        },
        stack=False,
    )
    st.subheader("Subscriptions")
    st.dataframe(
        [
            {
                "merchant": s.merchant,
                "amount €": s.amount,
                "cadence": s.cadence,
                "per year €": s.annual_cost,
                "price before": s.previous_amount or "",
                "status": "active" if s.active else f"stopped {s.last_date}",
            }
            for s in subs
        ],
        hide_index=True,
        width="stretch",
    )

with tab_chat:
    if not st.session_state.messages:
        st.markdown("Ask about your spending, subscriptions or savings. Try:")
        cols = st.columns(3)
        for i, q in enumerate(
            [
                "Quels sont mes abonnements ?",
                "Combien je peux épargner par mois ?",
                "Je veux 5000 € d'ici décembre 2027",
                "Simule un Livret A avec 200 € par mois pendant 24 mois",
                "Mes dépenses ont augmenté ?",
                "Quelle action dois-je acheter ?",
            ]
        ):
            if cols[i % 3].button(q, key=f"s{i}", width="stretch"):
                st.session_state.pending = q
                st.rerun()

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            r = m.get("resp")
            if r is None:
                continue
            tags = []
            if r.refused:
                tags.append(f'<span class="tag stop">refused: {r.refusal_reason.replace("_", " ")}</span>')
            elif r.numbers_verified:
                tags.append('<span class="tag ok">every figure verified</span>')
            for e in r.guard_events:
                if e.startswith("answer sent back"):
                    tags.append('<span class="tag warn">corrected before display</span>')
                if e.startswith("answer replaced"):
                    tags.append('<span class="tag stop">model text replaced by verified figures</span>')
            unit = "planner step(s), no model" if provider == "offline" else "model call(s)"
            tags.append(f'<span class="tag">{r.llm_calls} {unit} · {r.latency_ms} ms</span>')
            st.markdown(" ".join(tags), unsafe_allow_html=True)
            if r.figures:
                with st.expander(f"Proof: {len(r.figures)} figure(s), {len(r.transactions)} transaction(s)"):
                    st.dataframe(
                        [
                            {
                                "ref": ref,
                                "figure": f.name,
                                "value": f.value,
                                "unit": f.unit,
                                "formula": f.formula,
                                "transactions": len(f.transaction_ids),
                            }
                            for ref, f in r.figures.items()
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                    if r.transactions:
                        st.dataframe(
                            [
                                {
                                    "date": t.date,
                                    "label": t.label,
                                    "amount €": t.amount_cents / 100,
                                    "category": t.category,
                                }
                                for t in sorted(r.transactions.values(), key=lambda t: t.date)
                            ],
                            hide_index=True,
                            width="stretch",
                            height=240,
                        )
            if show_view and r.model_view:
                with st.expander("What the model saw (personal data masked)"):
                    for i, v in enumerate(r.model_view, 1):
                        st.caption(f"Call {i}")
                        st.code(v[:4000], language="json")

    question = st.chat_input("Your question") or st.session_state.pop("pending", None)
    if question:
        st.session_state.messages.append({"role": "user", "text": question})
        try:
            with st.spinner("Working on your accounts..."):
                resp = assistant.ask(question)
            st.session_state.messages.append({"role": "assistant", "text": resp.answer, "resp": resp})
        except LeakBlocked as e:
            st.session_state.messages.append({"role": "assistant", "text": f"Blocked for privacy: {e}"})
        except Exception as e:  # noqa: BLE001 - shown to the user
            st.session_state.messages.append({"role": "assistant", "text": f"Error: {type(e).__name__}: {e}"})
        st.rerun()
