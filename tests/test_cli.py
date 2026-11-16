def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    from nestegg.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 0
    assert main(["report", "--customer", "C001"]) == 0
    out = capsys.readouterr().out
    assert "NETFLIX.COM" in out and "price up from 13.49" in out and "stopped since" in out
    assert main(["plan", "--customer", "C001", "--target", "5000", "--by", "2027-12"]) == 0
    assert main(["simulate", "--monthly", "100", "--months", "12", "--start", "2027-01-01"]) == 0
    assert "interest 10.20 EUR" in capsys.readouterr().out
    assert main(["mask", "--customer", "C001", "--limit", "2"]) == 0
    assert "[IBAN_1]" in capsys.readouterr().out
    assert main(["forget", "--customer", "C001"]) == 0
    assert main(["report", "--customer", "C001"]) == 1
    assert (tmp_path / "nestegg.db").exists()
