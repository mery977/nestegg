"""Security guarantees, each one tested: masking, customer isolation, erasure."""

import json

import pytest

from nestegg.pii import Vault, find_leaks, redact
from nestegg.schemas import Transaction


def test_iban_card_email_phone_are_masked_and_restorable():
    vault = Vault("C1")
    text = (
        "VIR SEPA EMIS VERS FR76 3000 6000 0112 3456 7890 189 carte 4111 1111 1111 1111 "
        "contact jean.martin@exemple.fr tel 06 12 34 56 78"
    )
    masked = redact(text, vault)
    assert find_leaks(masked) == []
    assert "[IBAN_1]" in masked and "[CARD_1]" in masked and "[EMAIL_1]" in masked and "[PHONE_1]" in masked
    assert vault.restore(masked) == text


def test_invalid_numbers_are_not_mistaken_for_personal_data():
    vault = Vault("C1")
    text = "FACTURE 1234567890123 REF FR00 0000 0000 0000"  # fails Luhn and the IBAN checksum
    assert redact(text, vault) == text


def test_people_are_masked_in_transfer_labels_but_not_savings_or_salary_words():
    vault = Vault("C1")
    assert redact("VIR SEPA EMIS A MARIE DUPONT", vault) == "VIR SEPA EMIS A [PERSON_1]"
    assert (
        redact("VIR INST RECU DE M. LEA PETIT REMBOURSEMENT", vault) == "VIR INST RECU DE M. [PERSON_2] REMBOURSEMENT"
    )
    assert redact("VIR LIVRET A EPARGNE", vault) == "VIR LIVRET A EPARGNE"


def test_same_value_same_token_and_vaults_are_per_customer():
    a, b = Vault("C1"), Vault("C2")
    assert redact("VIR A MARIE DUPONT", a) == redact("VIR A MARIE DUPONT", a)
    redact("VIR A JEAN MARTIN", b)
    assert b.restore("[PERSON_1]") == "JEAN MARTIN" and a.restore("[PERSON_1]") == "MARIE DUPONT"


def test_no_friend_name_or_iban_survives_masking_on_full_demo_statements(store, demo_files):
    out, _ = demo_files
    for cid in ["C001", "C002", "C003"]:
        truth = json.loads((out / f"{cid.lower()}.truth.json").read_text(encoding="utf-8"))
        vault = Vault(cid)
        masked = "\n".join(redact(t.label, vault) for t in store.for_customer(cid).transactions())
        assert find_leaks(masked) == []
        for secret in truth["friends"] + truth["ibans"]:
            assert secret not in masked


def test_a_customer_view_cannot_see_other_customers(store):
    c1, c2 = store.for_customer("C001"), store.for_customer("C002")
    ids1, ids2 = {t.id for t in c1.transactions()}, {t.id for t in c2.transactions()}
    assert ids1 and ids2 and not ids1 & ids2
    assert all(t.customer_id == "C001" for t in c1.transactions())
    victim = next(iter(ids2))
    assert c1.set_category(victim, "Hacked", "test") is False  # cannot touch another customer's row
    assert {t.category for t in c2.transactions() if t.id == victim} != {"Hacked"}


def test_transactions_of_another_customer_cannot_be_written(store):
    stolen = Transaction(id="x", customer_id="C002", date="2026-01-01", amount_cents=-1, label="X")
    with pytest.raises(PermissionError):
        store.for_customer("C001").add([stolen])


def test_erasure_deletes_everything_about_one_customer_only(store):
    before_other = store.for_customer("C002").count()
    deleted = store.for_customer("C001").erase()
    assert deleted > 0 and store.for_customer("C001").count() == 0
    assert store.for_customer("C002").count() == before_other
    assert "C001" not in store.customers()


def test_a_customer_id_is_mandatory(store):
    with pytest.raises(ValueError):
        store.for_customer("  ")


def test_reimport_is_idempotent(store, demo_files):
    from nestegg.importers import read_csv

    _, paths = demo_files
    txs, _ = read_csv(paths[0], "C001")
    assert store.for_customer("C001").add(txs) == 0
