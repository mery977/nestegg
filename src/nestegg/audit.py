"""Audit trail: one JSON line per turn, in audit/YYYY-MM-DD.jsonl.

What is recorded: who (a pseudonymous customer reference), which model and where the data went, the masked
question and answer, the tools called, the figures cited, the guardrail events, cost indicators.
What is never recorded: names, IBANs, card numbers, e-mails or phone numbers (the masked text only), and the
token mapping. A final check refuses to write a line that would contain a personal value.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .pii import find_leaks

_SALT = os.environ.get("NESTEGG_AUDIT_SALT", "nestegg-demo-salt")


def customer_ref(customer_id: str) -> str:
    return hashlib.sha256(f"{_SALT}:{customer_id}".encode()).hexdigest()[:12]


def write(audit_dir: str | Path, record: dict) -> Path:
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}, ensure_ascii=False)
    if find_leaks(line):
        raise RuntimeError("audit record refused: it would contain personal data")
    path = Path(audit_dir) / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return path
