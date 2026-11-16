"""Guardrails, deterministic and testable. They run before and after the model, whatever the model.

Before:  personalised investment advice, other customers' data and prompt-injection attempts are refused
         without calling the model, with an explanation and what the assistant can do instead.
After:   every number of the answer must come from a tool result of this turn (or from the question);
         an answer that recommends buying or selling a financial product is blocked.
Also:    nothing is sent to a model if a personal value is still visible in the prompt (fail closed).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


SECURITIES = (
    r"(actions?|etf|trackers?|fonds|sicav|opcvm|obligations?|crypto\w*|bitcoin|ethereum|bourse|stocks?|shares?|"
    r"bonds?|funds?|scpi|options?|warrants?|forex|titres?)"
)
ADVICE = [
    rf"\b(quel(le)?s?|which|what)\b.{{0,30}}\b{SECURITIES}\b.{{0,40}}\b(acheter|vendre|choisir|prendre|investir|buy|sell|pick|choose|invest)",
    r"\b(dois|devrais|faut)[- ]?(je|il)?\b.{0,30}\b(acheter|vendre|investir|placer|garder)\b",
    r"\b(should|shall) i\b.{0,30}\b(buy|sell|invest|hold)\b",
    rf"\b(meilleure?s?|best|top)\b.{{0,20}}\b({SECURITIES}|placements?|investissements?|investments?)\b",
    rf"\b(conseille|recommande|recommend|suggest)\w*\b.{{0,30}}\b({SECURITIES}|placements?|investissements?|investments?)\b",
    rf"\b(acheter|vendre|buy|sell)\b.{{0,25}}\b({SECURITIES}|tesla|nvidia|apple|lvmh|airbus|total)\b",
    r"\b(ou|where)\b.{0,15}\b(placer|investir|invest|put)\b.{0,20}\b(argent|epargne|money|savings)\b",
]
OTHER_CUSTOMERS = [
    r"\b(autres?|tous les|all|other|every)\s+(clients?|customers?|utilisateurs?|users?|comptes?)\b",
    r"\bclient\s*(n[o°]?\s*)?[a-z]?\d{2,}\b",
    r"\bcustomer\s*(id\s*)?[a-z]?\d{2,}\b",
    r"\b(compte|account|donnees|data)\b.{0,20}\b(de|of)\b\s+(mon|ma|my)?\s*(voisin|collegue|neighbour|neighbor|colleague|ami|friend)",
]
INJECTION = [
    r"\b(ignore|oublie|forget|disregard)\b.{0,30}\b(instructions?|regles|rules|consignes|previous|precedentes)\b",
    r"\b(system prompt|prompt systeme|tes instructions|your instructions|developer mode|mode developpeur|jailbreak)\b",
    r"\b(tu es maintenant|you are now|act as|agis comme)\b",
]


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""  # investment_advice | other_customer | injection
    message: str = ""


REFUSALS = {
    "investment_advice": (
        "Je ne peux pas vous recommander d'acheter, de vendre ou de choisir un produit financier : c'est du conseil "
        "en investissement, une activité réglementée réservée aux conseillers agréés. Je peux en revanche analyser "
        "votre budget, calculer ce que vous pouvez épargner chaque mois et simuler un Livret A, un LDDS ou un LEP "
        "avec les taux officiels. Pour un conseil personnalisé, adressez-vous à un conseiller en investissements "
        "financiers (CIF) enregistré à l'ORIAS."
    ),
    "other_customer": (
        "Je n'ai accès qu'à vos propres comptes : je ne peux ni consulter ni comparer les données d'autres clients."
    ),
    "injection": (
        "Je ne peux pas modifier mes règles de fonctionnement. Je peux vous aider sur vos dépenses, vos abonnements "
        "et votre épargne."
    ),
}


def check_input(question: str) -> Verdict:
    text = _plain(question)
    for reason, patterns in (
        ("injection", INJECTION),
        ("other_customer", OTHER_CUSTOMERS),
        ("investment_advice", ADVICE),
    ):
        if any(re.search(p, text) for p in patterns):
            return Verdict(False, reason, REFUSALS[reason])
    return Verdict(True)


ADVICE_OUTPUT = [
    rf"\b(achetez|achete|acheter|vendez|vends|investissez|investis|buy|sell|invest in)\b.{{0,30}}\b{SECURITIES}\b",
    rf"\b(je vous|je te|i)\s+(conseille|recommande|recommend|suggest)\w*\b.{{0,40}}\b({SECURITIES}|placement)",
]


def check_output_advice(answer: str) -> bool:
    """True if the answer recommends a financial product (must be blocked)."""
    text = _plain(answer)
    return any(re.search(p, text) for p in ADVICE_OUTPUT)


NUMBER = re.compile(r"(?<![\w\[])(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\w\]])")
MONTHS = (
    r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|january|february|"
    r"march|april|may|june|july|august|september|october|november|december)"
)


def numbers_in(text: str, with_kind: bool = False) -> list:
    """Numbers a reader would take as figures. Years, dates, and token indexes ([PERSON_2]) are not figures.
    With `with_kind`, returns (value, is_money_or_rate) pairs: True when followed by EUR, euros or %."""
    text = re.sub(r"\[[A-Z]+_\d+\]", " ", text)
    text = re.sub(r"\b\d{4}-\d{2}(-\d{2})?\b", " ", text)  # ISO dates and months
    text = re.sub(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", " ", text)  # 31/12/2027
    plain = _plain(text.replace("€", " eur"))
    plain = re.sub(rf"\b\d{{1,2}}(er)?\s+{MONTHS}\b", " ", plain)  # "1er fevrier"
    plain = re.sub(rf"\b{MONTHS}\s+\d{{4}}\b", " ", plain)  # "decembre 2027"
    values = []
    for m in NUMBER.finditer(plain):
        raw = re.sub(r"[ \u00a0\u202f]", "", m.group(1))
        if "," in raw and "." not in raw:
            raw = raw.replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if 1990 <= v <= 2100 and float(v).is_integer():
            continue  # a year
        money = bool(re.match(r"\s*(eur|euros?|%|\$)", plain[m.end() :]))
        values.append((v, money) if with_kind else v)
    return values


def unverified_numbers(answer: str, allowed: set[float], question: str = "") -> list[float]:
    """Numbers of the answer that no tool returned (rounding tolerated: 357.14 may be written 357).
    Small whole numbers without a unit ("3 abonnements", "12 mois") are counts, not amounts."""
    allowed = set(allowed) | set(numbers_in(question))
    bad = []
    for v, money in numbers_in(answer, with_kind=True):
        if not money and v <= 12 and float(v).is_integer():
            continue
        if not any(abs(v - a) <= max(0.51, abs(a) * 0.005) for a in allowed):
            bad.append(v)
    return bad
