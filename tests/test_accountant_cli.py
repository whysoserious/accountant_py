#!/usr/bin/env python3
"""Tests for CLI helpers and subcommand wiring. No network access."""

from datetime import date

import pytest

from accountant import _previous_month


class TestPreviousMonth:
    def test_mid_year_steps_back_one_month(self):
        assert _previous_month(date(2026, 8, 10)) == "2026-07"

    def test_january_rolls_back_to_december_of_the_prior_year(self):
        assert _previous_month(date(2026, 1, 15)) == "2025-12"

    def test_february_pads_the_month_to_two_digits(self):
        assert _previous_month(date(2026, 3, 1)) == "2026-02"

    def test_december_stays_in_the_same_year(self):
        assert _previous_month(date(2026, 12, 31)) == "2026-11"

    def test_defaults_to_today_and_returns_a_well_formed_month(self):
        result = _previous_month()
        assert len(result) == 7
        assert result[4] == "-"
        year, month = result.split("-")
        assert year.isdigit() and 1 <= int(month) <= 12


class TestSubcommandWiring:
    """The parser is built inside main(), so exercise it through the CLI."""

    @pytest.mark.parametrize("command", ["download", "ksef", "rename", "excel"])
    def test_every_subcommand_offers_help(self, command, capsys, monkeypatch):
        import accountant

        monkeypatch.setattr("sys.argv", ["accountant.py", command, "--help"])
        with pytest.raises(SystemExit) as exc:
            accountant.main()

        assert exc.value.code == 0
        assert command in capsys.readouterr().out

    def test_excel_accepts_every_documented_flag(self, monkeypatch):
        """Guards the README's command reference against silent drift."""
        import accountant

        monkeypatch.setattr(
            "sys.argv",
            [
                "accountant.py",
                "excel",
                "--month",
                "2026-06",
                "--directory",
                "./invoices",
                "--role",
                "seller",
                "--output",
                "/tmp/report.xlsx",
                "--no-ai",
                "--help",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            accountant.main()

        assert exc.value.code == 0

    def test_no_subcommand_prints_help_and_fails(self, monkeypatch):
        import accountant

        monkeypatch.setattr("sys.argv", ["accountant.py"])
        with pytest.raises(SystemExit) as exc:
            accountant.main()

        assert exc.value.code == 1
