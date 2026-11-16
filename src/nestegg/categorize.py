"""Merchant normalisation and rule-based categorisation, explainable line by line.

Bank labels are noisy: "CB NETFLIX.COM 12/10 X4974", "PRLV SEPA FREE MOBILE ...", "PAIEMENT PAR CARTE
X1234 MONOPRIX PARIS 15". The merchant is extracted first, then matched against a rule table. Every
category says which rule produced it. Labels no rule recognises stay "A categoriser": in step 2 the agent
proposes a category, never silently.
"""

from __future__ import annotations

import re
import unicodedata

from .schemas import Transaction

UNCATEGORISED = "A categoriser"

# (category, keywords). Order matters: the first match wins, so specific rules come before generic ones.
RULES: list[tuple[str, list[str]]] = [
    ("Epargne", ["LIVRET A", "LDDS", "LEP ", "VIR LIVRET", "VERS LIVRET", "VIREMENT EPARGNE", "PEA "]),
    (
        "Revenus",
        [
            "SALAIRE",
            "PAIE ",
            "VIR SEPA RECU EMPLOYEUR",
            "CAF ",
            "POLE EMPLOI",
            "FRANCE TRAVAIL",
            "REMBOURSEMENT CPAM",
            "CPAM",
        ],
    ),
    (
        "Logement",
        [
            "LOYER",
            "EDF",
            "ENGIE",
            "TOTALENERGIES ELEC",
            "EAU DE PARIS",
            "VEOLIA",
            "ASSURANCE HABITATION",
            "FONCIA",
            "NEXITY",
            "CHARGES COPRO",
        ],
    ),
    (
        "Abonnements",
        [
            "NETFLIX",
            "SPOTIFY",
            "DEEZER",
            "DISNEY",
            "AMAZON PRIME",
            "PRIME VIDEO",
            "CANAL",
            "APPLE.COM",
            "ICLOUD",
            "YOUTUBE",
            "GOOGLE ONE",
            "CHATGPT",
            "OPENAI",
            "CLAUDE.AI",
            "BASIC FIT",
            "BASIC-FIT",
            "FITNESS PARK",
            "NEONESS",
            "AUDIBLE",
            "DAZN",
        ],
    ),
    ("Telecom", ["FREE MOBILE", "FREE TELECOM", "ORANGE", "SFR", "BOUYGUES TEL", "RED BY SFR", "SOSH"]),
    (
        "Transport",
        [
            "RATP",
            "NAVIGO",
            "SNCF",
            "UBER TRIP",
            "UBER *TRIP",
            "BOLT",
            "LIME",
            "TOTAL ACCESS",
            "ESSO",
            "SHELL",
            "BP ",
            "PARKING",
            "AUTOROUTE",
            "VINCI AUTOROUTES",
            "BLABLACAR",
            "TRANSDEV",
            "OUIGO",
        ],
    ),
    (
        "Restaurants",
        [
            "UBER EATS",
            "UBER *EATS",
            "DELIVEROO",
            "JUST EAT",
            "MCDONALD",
            "BURGER KING",
            "KFC",
            "STARBUCKS",
            "RESTAURANT",
            "BRASSERIE",
            "PIZZ",
            "SUSHI",
            "CAFE ",
            "BOULANGERIE",
            "PAUL ",
        ],
    ),
    (
        "Alimentation",
        [
            "CARREFOUR",
            "MONOPRIX",
            "FRANPRIX",
            "LIDL",
            "AUCHAN",
            "LECLERC",
            "INTERMARCHE",
            "PICARD",
            "CASINO",
            "ALDI",
            "NATURALIA",
            "BIOCOOP",
            "G20",
            "SUPER U",
            "HYPER U",
            "LA GRANDE EPICERIE",
        ],
    ),
    (
        "Shopping",
        [
            "AMAZON",
            "FNAC",
            "DARTY",
            "ZARA",
            "H&M",
            "UNIQLO",
            "DECATHLON",
            "IKEA",
            "SEPHORA",
            "VINTED",
            "ZALANDO",
            "LEROY MERLIN",
            "BOULANGER",
            "ALIEXPRESS",
            "SHEIN",
        ],
    ),
    ("Sante", ["PHARMACIE", "DOCTOLIB", "MEDECIN", "DR ", "DENTISTE", "MUTUELLE", "LABORATOIRE", "OPTIC"]),
    (
        "Loisirs",
        ["UGC", "PATHE", "CINEMA", "FNAC SPECTACLES", "TICKETMASTER", "MUSEE", "STEAM", "PLAYSTATION"],
    ),
    (
        "Voyages",
        ["AIRBNB", "BOOKING.COM", "AIR FRANCE", "EASYJET", "RYANAIR", "TRANSAVIA", "HOTEL", "EXPEDIA"],
    ),
    ("Frais bancaires", ["FRAIS", "COMMISSION", "COTISATION CARTE", "AGIOS", "COTIS ", "INTERETS DEBITEURS"]),
    ("Retraits", ["RETRAIT DAB", "RETRAIT GAB", "RETRAIT ", "DAB "]),
    ("Impots", ["DGFIP", "IMPOT", "TRESOR PUBLIC", "AMENDE"]),
    ("Virements", ["VIR ", "VIREMENT", "PAYLIB", "LYDIA", "WERO"]),
]

_NOISE = [
    r"\bPAIEMENT PAR CARTE\b",
    r"\bPAIEMENT CB\b",
    r"\bCARTE\b",
    r"^CB\*?\s*",
    r"\bCB\b",
    r"\bPRLV SEPA\b",
    r"\bPRELEVEMENT\b",
    r"\bPRLV\b",
    r"\bX\d{4}\b",
    r"\b\d{2}/\d{2}(/\d{2,4})?\b",
    r"\bFACTURE\b",
    r"\bECH(EANCE)?\b",
    r"\bREF\S*\b",
    r"\bMANDAT\b.*$",
    r"\b\d{5,}\b",
    r"\*",
]


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", text).strip()


def normalise_merchant(label: str) -> str:
    """'CB NETFLIX.COM 12/10 X4974' -> 'NETFLIX.COM'; 'PRLV SEPA FREE MOBILE ECH/151026' -> 'FREE MOBILE'."""
    text = _plain(label)
    for pattern in _NOISE:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -/.")
    # Keep the merchant, not the city or terminal suffix ("MONOPRIX PARIS 15" -> "MONOPRIX PARIS")
    words = text.split(" ")
    return " ".join(words[:3]) if words != [""] else _plain(label)[:30]


def categorise(label: str, amount_cents: int) -> tuple[str, str]:
    """Return (category, rule). The rule is the keyword that matched, for explainability."""
    text = " " + _plain(label) + " "
    for category, keywords in RULES:
        for kw in keywords:
            if kw in text:
                if category == "Revenus" and amount_cents < 0:
                    continue  # a refund label on a debit is not an income
                return category, f"keyword '{kw.strip()}'"
    if amount_cents > 0:
        return "Revenus", "credit without a known keyword"
    return UNCATEGORISED, "no rule matched"


def enrich(transactions: list[Transaction]) -> list[Transaction]:
    for t in transactions:
        t.merchant = normalise_merchant(t.label)
        t.category, t.category_rule = categorise(t.label, t.amount_cents)
    return transactions
