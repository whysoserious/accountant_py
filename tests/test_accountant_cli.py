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


class TestAllPipeline:
    """
    The `all` command chains the existing steps. These tests drive it with the
    real step handlers stubbed out, so no download, rename or API call happens.
    """

    def stub_steps(self, monkeypatch, calls, failing=None):
        import accountant

        def make(name):
            def handler(args):
                calls.append((name, args))
                return 1 if name == failing else 0

            return handler

        for name in ("ksef_command", "rename_command", "render_command", "excel_command"):
            monkeypatch.setattr(accountant, name, make(name))

    def run(self, monkeypatch, argv, calls, failing=None):
        import accountant

        self.stub_steps(monkeypatch, calls, failing)
        monkeypatch.setattr(accountant, "_default_directory", lambda _: "./invoices")
        monkeypatch.setattr("sys.argv", ["accountant.py", "all"] + argv)
        with pytest.raises(SystemExit) as exc:
            accountant.main()
        return exc.value.code

    def test_runs_every_step_in_order(self, monkeypatch, capsys):
        calls = []
        code = self.run(monkeypatch, ["--month", "2026-06"], calls)

        assert code == 0
        assert [name for name, _ in calls] == [
            "ksef_command",
            "rename_command",
            "render_command",
            "excel_command",
        ]

    def test_the_same_month_reaches_download_and_report(self, monkeypatch):
        """
        The individual defaults disagree -- ksef uses the current month, excel
        the previous one -- so a pipeline must pass the month explicitly or it
        would download one month and report another.
        """
        calls = []
        self.run(monkeypatch, ["--month", "2026-06"], calls)

        by_name = dict(calls)
        assert by_name["ksef_command"].month == "2026-06"
        assert by_name["excel_command"].month == "2026-06"

    def test_without_a_month_every_step_gets_the_same_resolved_one(self, monkeypatch):
        calls = []
        self.run(monkeypatch, [], calls)

        by_name = dict(calls)
        assert by_name["ksef_command"].month == by_name["excel_command"].month
        assert by_name["ksef_command"].month == _previous_month()

    def test_skip_download_starts_at_rename(self, monkeypatch):
        calls = []
        code = self.run(monkeypatch, ["--skip-download"], calls)

        assert code == 0
        assert [name for name, _ in calls][0] == "rename_command"
        assert "ksef_command" not in [name for name, _ in calls]

    def test_stops_at_the_first_failure(self, monkeypatch):
        calls = []
        code = self.run(monkeypatch, [], calls, failing="rename_command")

        assert code == 1
        assert [name for name, _ in calls] == ["ksef_command", "rename_command"]

    def test_propagates_the_failing_step_exit_code(self, monkeypatch):
        calls = []
        assert self.run(monkeypatch, [], calls, failing="ksef_command") == 1

    def test_flags_are_passed_through_to_the_right_steps(self, monkeypatch):
        calls = []
        self.run(
            monkeypatch,
            ["--role", "seller", "--no-ai", "--bulk", "--output", "/tmp/r.xlsx"],
            calls,
        )

        by_name = dict(calls)
        assert by_name["ksef_command"].role == "seller"
        assert by_name["ksef_command"].bulk is True
        assert by_name["excel_command"].no_ai is True
        assert by_name["excel_command"].output == "/tmp/r.xlsx"
        assert by_name["excel_command"].role == "seller"

    def test_rename_never_receives_a_file_list(self, monkeypatch):
        """rename takes --files or --directory; the pipeline always uses a directory."""
        calls = []
        self.run(monkeypatch, [], calls)

        rename_args = dict(calls)["rename_command"]
        assert rename_args.files is None
        assert rename_args.directory

    def test_reports_progress_for_each_step(self, monkeypatch, capsys):
        calls = []
        self.run(monkeypatch, ["--month", "2026-06"], calls)

        out = capsys.readouterr().out
        assert "Step 1/4" in out and "Step 4/4" in out
        assert "2026-06" in out

    def test_help_lists_the_command(self, capsys, monkeypatch):
        import accountant

        monkeypatch.setattr("sys.argv", ["accountant.py", "all", "--help"])
        with pytest.raises(SystemExit) as exc:
            accountant.main()
        assert exc.value.code == 0
        assert "--skip-download" in capsys.readouterr().out
