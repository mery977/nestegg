"""Masking of personal data before any text reaches a language model.

Values are replaced by stable tokens ([IBAN_1], [PERSON_2]...). The mapping stays server-side, in a vault
scoped to one customer, so answers can be restored for that customer only. What is masked:

- IBANs (any country, checksum-validated) and French RIB-style account numbers
- payment card numbers (Luhn-validated); already-masked forms such as X4974 are left as they are
- e-mail addresses and phone numbers (French and international formats)
- names of people in transfer labels ("VIR SEPA A MARIE DUPONT", "VIREMENT DE M. JEAN MARTIN")
- names of organisations in the same position (employer, landlord...), with their own [ORG_n] tokens
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE = re.compile(r"(?<![\w+])(?:\+33[ .]?[1-9]|0[1-9])(?:[ .-]?\d{2}){4}\b")
# A transfer to/from a person: keyword, optional civility, then 2 to 4 capitalised words.
PERSON = re.compile(
    r"(?P<prefix>\b(?:VIR(?:EMENT)?(?: SEPA)?(?: INST(?:ANTANE)?)?(?: EMIS| RECU)?|PAYLIB|LYDIA|WERO)"
    r"(?:\s+(?:A|À|DE|POUR|VERS|FROM|TO))?\s*/?(?:DE:|A:)?\s*)"
    r"(?P<civ>(?:M\.|MR|MME|MLLE|MONSIEUR|MADAME)\s+)?"
    r"(?P<name>[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ'-]+(?:\s+[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ'-]+){1,3})"
)
# Words that follow a transfer keyword but are not people.
NOT_NAMES = {
    "LIVRET",
    "SALAIRE",
    "LOYER",
    "EPARGNE",
    "COMPTE",
    "PERMANENT",
    "SEPA",
    "INST",
    "INSTANTANE",
    "EMIS",
    "RECU",
    "CAF",
    "CPAM",
    "DGFIP",
    "IMPOT",
    "EMPLOYEUR",
    "REMBOURSEMENT",
    "LDDS",
    "LEP",
    "PEA",
    "MOTIF",
    "REF",
}


# A transfer counterparty containing one of these words is an organisation, not a person.
ORG_MARKERS = {
    "SAS",
    "SASU",
    "SARL",
    "SA",
    "EURL",
    "SCI",
    "SNC",
    "SCOP",
    "GIE",
    "INC",
    "LTD",
    "LLC",
    "GMBH",
    "BV",
    "PLC",
    "CONSEIL",
    "CONSULTING",
    "GROUP",
    "GROUPE",
    "HOLDING",
    "FRANCE",
    "BANQUE",
    "BANK",
    "ASSURANCE",
    "ASSURANCES",
    "MUTUELLE",
    "ASSOCIATION",
    "SERVICES",
    "SOLUTIONS",
    "SYSTEMS",
    "TECHNOLOGIES",
    "INDUSTRIES",
    "INTERNATIONAL",
    "AGENCE",
    "CABINET",
    "BRASSERIE",
    "RESTAURANT",
    "HOTEL",
    "STUDIO",
    "LABS",
    "SOCIETE",
    "ENTREPRISE",
    "MAIRIE",
}


def _iban_ok(candidate: str) -> bool:
    s = candidate.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in moved)
    return int(digits) % 97 == 1


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


@dataclass
class Vault:
    """Token <-> value mapping for one customer. Never sent to a model, never logged."""

    customer_id: str
    _by_value: dict[str, str] = field(default_factory=dict)
    _by_token: dict[str, str] = field(default_factory=dict)

    def token(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self._by_value:
            n = sum(1 for t in self._by_token if t.startswith(f"[{kind}_")) + 1
            tok = f"[{kind}_{n}]"
            self._by_value[key], self._by_token[tok] = tok, value
        return self._by_value[key]

    def restore(self, text: str) -> str:
        return re.sub(r"\[[A-Z]+_\d+\]", lambda m: self._by_token.get(m.group(0), m.group(0)), text)

    def __len__(self) -> int:
        return len(self._by_token)


def redact(text: str, vault: Vault) -> str:
    """Return the text with every personal value replaced by a token of the customer's vault."""
    text = IBAN.sub(lambda m: vault.token("IBAN", m.group(0)) if _iban_ok(m.group(0)) else m.group(0), text)
    text = CARD.sub(lambda m: vault.token("CARD", m.group(0).strip()) if _luhn_ok(m.group(0)) else m.group(0), text)
    text = EMAIL.sub(lambda m: vault.token("EMAIL", m.group(0)), text)
    text = PHONE.sub(lambda m: vault.token("PHONE", m.group(0)), text)

    def person(m: re.Match) -> str:
        words = m.group("name").split()
        if words[0].upper() in NOT_NAMES:
            return m.group(0)
        # "LEA PETIT REMBOURSEMENT": the name stops at the first word that is not part of a name
        cut = next((i for i, w in enumerate(words[1:], 1) if w.upper() in NOT_NAMES), len(words))
        name, rest = " ".join(words[:cut]), " ".join(words[cut:])
        if len(words[:cut]) < 2:
            return m.group(0)
        kind = "ORG" if any(w.upper() in ORG_MARKERS for w in words[:cut]) else "PERSON"
        return m.group("prefix") + (m.group("civ") or "") + vault.token(kind, name) + (" " + rest if rest else "")

    return PERSON.sub(person, text)


def find_leaks(text: str) -> list[str]:
    """Personal values still present in a text (used by tests and by the guard before each model call)."""
    leaks = [m.group(0) for m in IBAN.finditer(text) if _iban_ok(m.group(0))]
    leaks += [m.group(0) for m in CARD.finditer(text) if _luhn_ok(m.group(0))]
    leaks += [m.group(0) for m in EMAIL.finditer(text)]
    leaks += [m.group(0) for m in PHONE.finditer(text)]
    return leaks
