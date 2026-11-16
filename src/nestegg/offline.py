"""Offline planner: a deterministic stand-in for a language model, so the whole agent runs without any key.

It reads the same JSON payload a real model receives, picks a tool from keywords, then writes a French
answer from the tool's figures. It is deliberately simple: it exists for tests, demos and CI, and to show
that guardrails and verification do not depend on the model.
"""

from __future__ import annotations

import json
import re
import unicodedata

from .llm import LLM

MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


CADENCE_FR = {"weekly": "hebdomadaire", "monthly": "mensuel", "yearly": "annuel"}


def pct(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " %"


def eur(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " €"


def _amounts(q: str) -> list[float]:
    return [float(x.replace(" ", "").replace(",", ".")) for x in re.findall(r"(\d[\d ]*(?:[.,]\d+)?)\s*eur", q)]


def _intent(q: str) -> tuple[str, dict]:
    if re.search(r"livret a|ldds|\blep\b|simul|interets?", q):
        product = "ldds" if "ldds" in q else "lep" if re.search(r"\blep\b", q) else "livret_a"
        monthly = (_amounts(q) or [100.0])[0]
        m = re.search(r"(\d+)\s*(mois|ans?)", q)
        months = int(m.group(1)) * (12 if m and m.group(2).startswith("an") else 1) if m else 12
        return "simulate_regulated_savings", {"product": product, "monthly_eur": monthly, "months": months}
    if re.search(r"objectif|atteindre|d.ici|goal|reach|pour (un|une) |je veux|dans \d+ mois", q) and _amounts(q):
        deadline = None
        m = re.search(r"(" + "|".join(MONTHS) + r")\s+(\d{4})", q)
        if m:
            deadline = f"{m.group(2)}-{MONTHS[m.group(1)]:02d}"
        m2 = re.search(r"(\d{4})-(\d{2})", q)
        if m2:
            deadline = f"{m2.group(1)}-{m2.group(2)}"
        m3 = re.search(r"dans (\d+) mois", q)
        args = {"target_eur": max(_amounts(q)), "deadline": deadline or "+12"}
        if m3:
            args["deadline"] = f"+{m3.group(1)}"
        return "goal_plan", args
    m = re.search(r"(?:chez|at)\s+([a-z0-9.]{3,})", q)
    if m and re.search(r"combien|how much|depense", q):
        return "search_transactions", {"query": m.group(1)}
    if re.search(r"abonnement|subscription|netflix|spotify|recurrent", q):
        return "subscriptions", {}
    if re.search(r"augment|derive|hausse|plus que d.habitude|increase", q):
        return "spending_drifts", {}
    if "frais" in q or "fees" in q:
        return "bank_fees", {}
    if re.search(r"epargner|economiser|mettre de cote|capacite|save|mettre de l.argent", q):
        return "savings_capacity", {}
    if re.search(r"depens|categor|ou part|spend|budget|poste", q):
        return "spending_by_category", {}
    return "overview", {}


def _compose(tool: str, r: dict) -> tuple[str, list[str]]:
    if "error" in r:
        return f"Je n'ai pas pu calculer cela : {r['error']}", []
    if tool == "subscriptions":
        active = [s for s in r["subscriptions"] if s["active"]]
        lines = [f"Vous avez {len(active)} abonnements actifs, pour {eur(r['yearly_total']['value'])} par an."]
        refs = [r["yearly_total"]["ref"]]
        for s in active:
            cadence = CADENCE_FR.get(s["cadence"], s["cadence"])
            line = f"- {s['merchant']} : {eur(s['amount']['value'])} ({cadence}), {eur(s['yearly_cost']['value'])}/an"
            refs += [s["amount"]["ref"], s["yearly_cost"]["ref"]]
            if "previous_amount" in s and s["previous_amount"]["value"] < s["amount"]["value"]:
                line += f", en hausse (avant : {eur(s['previous_amount']['value'])})"
                refs.append(s["previous_amount"]["ref"])
            lines.append(line)
        stopped = [s["merchant"] for s in r["subscriptions"] if not s["active"]]
        if stopped:
            lines.append("\nArrêtés : " + ", ".join(stopped) + ".")
        return "\n".join(lines), refs
    if tool == "savings_capacity":
        f = r["savings_capacity"]
        return (
            f"Sur vos derniers mois complets, il vous reste en médiane {eur(f['value'])} par mois après vos "
            "dépenses : c'est votre capacité d'épargne réelle."
        ), [f["ref"]]
    if tool == "spending_by_category":
        top = r["categories"][:4]
        text = (
            f"En {r['month']}, vos principaux postes de dépense : "
            + ", ".join(f"{c['category']} {eur(c['value'])}" for c in top)
            + "."
        )
        return text, [c["ref"] for c in top]
    if tool == "spending_drifts":
        if not r["drifts"]:
            return "Aucune dépense n'a nettement dérivé le dernier mois.", []
        parts = [f"{d['category']} (+{pct(d['increase_pct']['value'])} en {d['month']})" for d in r["drifts"]]
        return "Ces dépenses ont nettement augmenté : " + ", ".join(parts) + ".", [
            d["increase_pct"]["ref"] for d in r["drifts"]
        ]
    if tool == "bank_fees":
        f = r["bank_fees"]
        return f"Vous avez payé {eur(f['value'])} de frais bancaires sur la période.", [f["ref"]]
    if tool == "goal_plan":
        e, n, s, cap = r["goal_monthly_effort"], r["goal_months"], r["goal_share_of_capacity"], r["capacity"]
        text = (
            f"Pour atteindre votre objectif, il faut mettre {eur(e['value'])} de côté par mois pendant "
            f"{int(n['value'])} mois, soit {pct(s['value'])} de votre capacité d'épargne actuelle "
            f"({eur(cap['value'])} par mois)."
        )
        refs = [e["ref"], n["ref"], s["ref"], cap["ref"]]
        if "goal_gap" in r:
            text += f" Il manque {eur(r['goal_gap']['value'])} par mois : il faudrait réduire certaines dépenses."
            refs.append(r["goal_gap"]["ref"])
        return text, refs
    if tool == "simulate_regulated_savings":
        text = (
            f"Simulation {r['product']} au taux officiel de {pct(r['rate']['value'])} : vous auriez versé "
            f"{eur(r['deposits']['value'])} et gagné {eur(r['interest']['value'])} d'intérêts, pour un solde de "
            f"{eur(r['final_balance']['value'])}. Le taux est révisé deux fois par an : ce n'est pas une promesse."
        )
        return text, [r[k]["ref"] for k in ("rate", "deposits", "interest", "final_balance")]
    if tool == "search_transactions":
        t = r["total_spent"]
        return f"J'ai trouvé {r['count']} opérations, pour un total de {eur(t['value'])}.", [t["ref"]]
    m = r["months"][-1]
    return (
        f"En {m['month']} : revenus {eur(m['income']['value'])}, dépenses {eur(m['spending']['value'])}, "
        f"reste {eur(m['net']['value'])}."
    ), [m[k]["ref"] for k in ("income", "spending", "net")]


class OfflinePlanner(LLM):
    name = "offline"

    def __init__(self):
        super().__init__("rules")

    def _complete(self, system: str, user: str) -> str:
        payload = json.loads(user)
        q = _plain(payload["question"].replace("€", " eur "))
        if not payload["steps"]:
            tool, args = _intent(q)
            if tool == "goal_plan" and str(args["deadline"]).startswith("+"):
                args["deadline"] = _relative_deadline(int(str(args["deadline"])[1:]))
            return json.dumps({"tool": tool, "args": args})
        last = payload["steps"][-1]
        text, refs = _compose(last["tool"], last["result"])
        return json.dumps({"answer": text, "figures": refs}, ensure_ascii=False)


def _relative_deadline(months: int) -> str:
    from datetime import date

    today = date.today()
    total = today.year * 12 + today.month - 1 + months
    return f"{total // 12}-{total % 12 + 1:02d}"
