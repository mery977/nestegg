"""Import bank statement exports (CSV) whatever the bank: encoding, separator, decimal comma, date format,
column names and debit/credit layouts are detected, never assumed.

Supported layouts (detected from the header):
- one signed amount column      (Montant / Amount)
- separate debit and credit     (Débit / Crédit)
Column names are matched in French and English, without accents or case, so exports from the usual French
banks and neobanks import without configuration. Every skipped row is reported with its reason.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .schemas import Transaction

DATE_COLUMNS = [
    "date operation",
    "date de l'operation",
    "dateop",
    "date",
    "booking date",
    "started date",
    "date comptable",
    "transaction date",
    "completed date",
    "date de valeur",
    "dateval",
]
LABEL_COLUMNS = [
    "libelle",
    "libelle operation",
    "label",
    "description",
    "detail",
    "wording",
    "libelle simplifie",
    "intitule",
    "operation",
]
AMOUNT_COLUMNS = ["montant", "amount", "montant (eur)", "montant eur", "valeur"]
DEBIT_COLUMNS = ["debit", "debit (eur)", "debit eur", "montant debit"]
CREDIT_COLUMNS = ["credit", "credit (eur)", "credit eur", "montant credit"]
DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"]


@dataclass
class ImportReport:
    rows: int = 0
    imported: int = 0
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (line number, reason)
    layout: dict = field(default_factory=dict)


def _key(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", name.strip().strip('"').lower())


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("unreadable file encoding")


def _find(header: list[str], candidates: list[str]) -> int | None:
    keys = [_key(h) for h in header]
    for cand in candidates:  # exact names first, in priority order
        if cand in keys:
            return keys.index(cand)
    for cand in candidates:  # then "Montant en euros", "Date opération (J)"...
        for i, k in enumerate(keys):
            if k.startswith(cand):
                return i
    return None


def parse_amount(text: str) -> int | None:
    """'-1 234,56 €', '1,234.56', '(12.30)', '+45' -> cents. None if empty."""
    t = (text or "").strip().replace(" ", " ").replace(" ", " ").replace("€", "").replace("EUR", "")
    if not t:
        return None
    negative = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("()+- ").replace(" ", "").replace("'", "")
    if "," in t and "." in t:  # the last separator is the decimal one
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = f"{head.replace(',', '')}.{tail}" if len(tail) != 3 else t.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        raise ValueError(f"not an amount: {text!r}")
    whole, _, dec = t.partition(".")
    cents = int(whole) * 100 + int((dec + "00")[:2])
    return -cents if negative else cents


def parse_date(text: str) -> date:
    t = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {text!r}")


def transaction_id(customer_id: str, d: date, cents: int, label: str, occurrence: int) -> str:
    raw = f"{customer_id}|{d.isoformat()}|{cents}|{label}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def read_csv(data: bytes | str | Path, customer_id: str) -> tuple[list[Transaction], ImportReport]:
    if isinstance(data, Path):
        data = data.read_bytes()
    text = _decode(data) if isinstance(data, bytes) else data
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Some banks put account information above the table: the header is the first line with a date column.
    start = next((i for i, ln in enumerate(lines[:30]) if re.search(r"date", _key(ln))), 0)
    body = "\n".join(lines[start:])
    try:
        dialect = csv.Sniffer().sniff(body.splitlines()[0], delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    rows = list(csv.reader(io.StringIO(body), delimiter=delimiter))
    header, data_rows = rows[0], rows[1:]

    i_date, i_label = _find(header, DATE_COLUMNS), _find(header, LABEL_COLUMNS)
    i_amount = _find(header, AMOUNT_COLUMNS)
    i_debit, i_credit = _find(header, DEBIT_COLUMNS), _find(header, CREDIT_COLUMNS)
    if i_date is None or i_label is None or (i_amount is None and i_debit is None and i_credit is None):
        raise ValueError(f"unrecognised statement layout, columns found: {header}")

    report = ImportReport(
        rows=len(data_rows),
        layout={
            "delimiter": delimiter,
            "date": header[i_date],
            "label": header[i_label],
            "amount": header[i_amount] if i_amount is not None else None,
            "debit": header[i_debit] if i_debit is not None else None,
            "credit": header[i_credit] if i_credit is not None else None,
        },
    )
    seen: Counter = Counter()
    out: list[Transaction] = []
    for n, row in enumerate(data_rows, start=start + 2):
        try:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            d = parse_date(row[i_date])
            label = re.sub(r"\s+", " ", row[i_label]).strip()
            if i_amount is not None and row[i_amount].strip():
                cents = parse_amount(row[i_amount])
            else:
                debit = parse_amount(row[i_debit]) if i_debit is not None else None
                credit = parse_amount(row[i_credit]) if i_credit is not None else None
                cents = (credit or 0) - abs(debit or 0)
            if not label:
                raise ValueError("empty label")
        except (ValueError, IndexError) as e:
            report.skipped.append((n, str(e)))
            continue
        seen[(d, cents, label)] += 1
        out.append(
            Transaction(
                id=transaction_id(customer_id, d, cents, label, seen[(d, cents, label)]),
                customer_id=customer_id,
                date=d,
                amount_cents=cents,
                label=label,
            )
        )
    report.imported = len(out)
    return out, report
