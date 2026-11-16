import pytest

from nestegg.importers import parse_amount, parse_date, read_csv


@pytest.mark.parametrize(
    "text, cents",
    [
        ("-1 234,56 €", -123456),
        ("1,234.56", 123456),
        ("1.234,56", 123456),
        ("-12,3", -1230),
        ("(12.30)", -1230),
        ("+45", 4500),
        ("2 650,00", 265000),
        ("", None),
    ],
)
def test_amounts_in_every_bank_format(text, cents):
    assert parse_amount(text) == cents


def test_dates_in_every_bank_format():
    for text in ["28/10/2026", "2026-10-28", "28-10-2026", "28.10.2026", "2026-10-28 10:00:00"]:
        assert str(parse_date(text)) == "2026-10-28"


def test_three_layouts_import_without_losing_a_row(demo_files):
    _, paths = demo_files
    for path in paths:
        txs, report = read_csv(path, "X")
        assert report.imported == report.rows > 250 and not report.skipped


def test_debit_credit_layout_with_account_header_lines():
    csv = (
        "Compte de chèques n° XXXX;;;\nPériode;du 01/10/2026 au 31/10/2026;;\n"
        "Date opération;Libellé opération;Débit;Crédit\n"
        "02/10/2026;CB MONOPRIX;45,30;\n28/10/2026;VIR SALAIRE;;2 650,00\n"
    ).encode("cp1252")
    txs, report = read_csv(csv, "X")
    assert [t.amount_cents for t in txs] == [-4530, 265000]
    assert report.layout["debit"] == "Débit"


def test_bad_rows_are_reported_not_silently_dropped():
    txs, report = read_csv("Date;Libellé;Montant\n01/10/2026;OK;-10,00\npas une date;X;-1\n05/10/2026;Y;abc\n", "X")
    assert len(txs) == 1 and [line for line, _ in report.skipped] == [3, 4]


def test_unknown_layout_is_refused_with_the_columns_found():
    with pytest.raises(ValueError, match="unrecognised statement layout"):
        read_csv("Foo;Bar\n1;2\n", "X")


def test_identical_operations_on_the_same_day_are_both_kept():
    txs, _ = read_csv("Date;Libellé;Montant\n01/10/2026;CB RATP;-2,15\n01/10/2026;CB RATP;-2,15\n", "X")
    assert len({t.id for t in txs}) == 2
