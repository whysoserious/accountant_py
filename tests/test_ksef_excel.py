#!/usr/bin/env python3
"""
Tests for the accountant Excel report.

Everything here runs offline. Workbook assembly is exercised with no API access
at all; the description step is driven through a stub client so the prompt
contract can be asserted without spending a token.
"""

from decimal import Decimal

import pytest
from openpyxl import load_workbook

import ksef_excel
from ksef_excel import (
    COLUMN_HEADERS,
    build_workbook,
    describe_invoices,
    filter_by_month,
    format_date,
    format_positions,
    load_records,
    parse_invoice_xml,
    write_report,
)

from tests.fixtures.synthetic import (
    BUYER_NAME,
    BUYER_NIP,
    SELLER_NAME,
    SELLER_NIP,
    build_fa3_xml,
    default_positions,
    mixed_rate_positions,
)

KSEF_NUMBER = "1000000000-20260625-AAAAAAAAAAAA-01"


class StubMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class StubMessages:
    """Records every call so the prompt contract can be asserted."""

    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [])
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return StubMessage(self.replies.pop(0) if self.replies else "opis")


class StubClient:
    def __init__(self, replies=None, error=None):
        self.messages = StubMessages(replies=replies, error=error)


class TestColumnLayout:
    def test_headers_match_the_accountants_sheet_then_the_two_additions(self):
        assert COLUMN_HEADERS == [
            "NIP",
            "Kontrahent",
            "Numer KSeF",
            "Numer dokumentu",
            "Data wystawienia",
            "Netto",
            "VAT",
            "Brutto",
            "Waluta",
            "Opis faktury - czego dotyczy w działalnosci",
            "Nazwa pliku",
            "pozycje",
        ]

    def test_there_are_exactly_twelve_columns(self):
        assert len(COLUMN_HEADERS) == 12


class TestParseInvoiceXml:
    def test_extracts_the_scalar_fields(self):
        record = parse_invoice_xml(build_fa3_xml(ksef_number=KSEF_NUMBER))

        assert record.ksef_number == KSEF_NUMBER
        assert record.document_number == "FS 1/2026"
        assert record.issue_date == "2026-06-25"
        assert record.currency == "PLN"

    def test_is_namespace_agnostic(self):
        bare = parse_invoice_xml(build_fa3_xml(ksef_number=KSEF_NUMBER))
        prefixed = parse_invoice_xml(build_fa3_xml(ksef_number=KSEF_NUMBER, prefix="tns:"))

        assert prefixed.ksef_number == bare.ksef_number
        assert prefixed.counterparty == bare.counterparty
        assert prefixed.net == bare.net
        assert len(prefixed.positions) == len(bare.positions)

    def test_buyer_role_reports_the_seller_as_counterparty(self):
        record = parse_invoice_xml(build_fa3_xml(), role="buyer")
        assert record.counterparty == SELLER_NAME
        assert record.nip == SELLER_NIP

    def test_seller_role_reports_the_buyer_as_counterparty(self):
        record = parse_invoice_xml(build_fa3_xml(), role="seller")
        assert record.counterparty == BUYER_NAME
        assert record.nip == BUYER_NIP

    def test_sums_single_rate_totals(self):
        record = parse_invoice_xml(build_fa3_xml(positions=default_positions()))

        assert record.net == Decimal("200.00")
        assert record.vat == Decimal("46.00")
        assert record.gross == Decimal("246.00")

    def test_sums_across_every_vat_rate(self):
        """P_13_x / P_14_x pairs must all be added, not just the first."""
        record = parse_invoice_xml(build_fa3_xml(positions=mixed_rate_positions()))

        assert record.net == Decimal("300.00")
        assert record.vat == Decimal("33.00")
        assert record.gross == Decimal("333.00")

    def test_falls_back_to_gross_minus_net_without_rate_totals(self):
        record = parse_invoice_xml(
            build_fa3_xml(positions=default_positions(), include_rate_totals=False)
        )

        assert record.gross == Decimal("246.00")
        assert record.vat == Decimal("46.00")

    def test_parses_line_items(self):
        record = parse_invoice_xml(build_fa3_xml(positions=default_positions()))

        assert len(record.positions) == 2
        first, second = record.positions
        assert first.name == "Usluga hostingu"
        assert first.unit == "szt."
        assert first.quantity == Decimal("1")
        assert first.net == Decimal("100.00")
        assert first.vat_rate == "23"
        assert second.name == "Domena .pl"
        assert second.quantity == Decimal("2")

    def test_missing_ksef_number_yields_empty_string_not_a_crash(self):
        record = parse_invoice_xml(build_fa3_xml(include_ksef_number=False))
        assert record.ksef_number == ""

    def test_invoice_without_line_items_still_parses(self):
        record = parse_invoice_xml(build_fa3_xml(positions=[]))
        assert record.positions == []
        assert record.net == Decimal("0.00")

    def test_malformed_xml_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_invoice_xml(b"<Faktura><unclosed>")

    def test_description_starts_empty(self):
        record = parse_invoice_xml(build_fa3_xml())
        assert record.description == ""


class TestFormatPositions:
    def test_numbers_each_item_on_its_own_line(self):
        record = parse_invoice_xml(build_fa3_xml(positions=default_positions()))
        text = format_positions(record.positions)

        lines = text.split("\n")
        assert len(lines) == 2
        assert (
            lines[0]
            == "1. Usluga hostingu — 1 szt. × 100.00 = netto 100.00, VAT 23%, brutto 123.00"
        )
        assert lines[1] == "2. Domena .pl — 2 szt. × 50.00 = netto 100.00, VAT 23%, brutto 123.00"

    def test_empty_positions_give_an_empty_cell(self):
        assert format_positions([]) == ""

    def test_renders_non_numeric_vat_rate_verbatim(self):
        """Exempt and out-of-scope invoices carry codes like 'zw' rather than a number."""
        from tests.fixtures.synthetic import Position

        xml = build_fa3_xml(positions=[Position(name="Usluga medyczna", vat_rate="zw")])
        record = parse_invoice_xml(xml)
        text = format_positions(record.positions)

        assert "VAT zw" in text
        assert "%" not in text

    def test_strips_trailing_zeros_from_quantity(self):
        from tests.fixtures.synthetic import Position

        xml = build_fa3_xml(positions=[Position(name="Usluga", quantity="1.000")])
        record = parse_invoice_xml(xml)
        assert "1 szt." in format_positions(record.positions)


class TestFormatDate:
    def test_converts_iso_to_polish_display_order(self):
        assert format_date("2026-06-25") == "25.06.2026"

    def test_tolerates_a_datetime_value(self):
        assert format_date("2026-06-25T10:30:00Z") == "25.06.2026"

    def test_passes_through_an_unparseable_value(self):
        assert format_date("nieznana") == "nieznana"

    def test_passes_through_empty(self):
        assert format_date("") == ""


class TestLoadRecords:
    def write_pair(self, directory, stem, xml_bytes, with_pdf=True):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stem}.xml").write_bytes(xml_bytes)
        if with_pdf:
            (directory / f"{stem}.pdf").write_bytes(b"%PDF-1.4 stub")

    def test_reads_every_xml_under_the_directory(self, tmp_path, logger):
        out = tmp_path / "output"
        self.write_pair(out, "2026-06-25, A, FS 1, ksef", build_fa3_xml(invoice_number="FS 1"))
        self.write_pair(out, "2026-06-26, B, FS 2, ksef", build_fa3_xml(invoice_number="FS 2"))

        records = load_records(str(tmp_path), logger=logger)

        assert {r.document_number for r in records} == {"FS 1", "FS 2"}

    def test_derives_the_local_pdf_filename_from_the_sidecar(self, tmp_path, logger):
        out = tmp_path / "output"
        stem = "2026-06-25, Dostawca, FS 1, ksef"
        self.write_pair(out, stem, build_fa3_xml())

        records = load_records(str(tmp_path), logger=logger)

        assert records[0].filename == f"{stem}.pdf"

    def test_keeps_the_row_when_the_pdf_is_missing(self, tmp_path, logger):
        """
        PDF and XML collisions are resolved independently upstream, so the pair
        can drift. Losing an invoice over a filename mismatch is worse than
        reporting a filename that is not on disk.
        """
        out = tmp_path / "output"
        stem = "2026-06-25, Dostawca, FS 1, ksef"
        self.write_pair(out, stem, build_fa3_xml(), with_pdf=False)

        records = load_records(str(tmp_path), logger=logger)

        assert len(records) == 1
        assert records[0].filename == f"{stem}.pdf"

    def test_skips_an_unparseable_xml_and_continues(self, tmp_path, logger):
        out = tmp_path / "output"
        self.write_pair(out, "good", build_fa3_xml(invoice_number="FS OK"))
        (out / "broken.xml").write_bytes(b"<Faktura><unclosed>")

        records = load_records(str(tmp_path), logger=logger)

        assert len(records) == 1
        assert records[0].document_number == "FS OK"

    def test_returns_empty_list_for_a_directory_with_no_xml(self, tmp_path, logger):
        (tmp_path / "output").mkdir()
        assert load_records(str(tmp_path), logger=logger) == []

    def test_returns_empty_list_for_a_missing_directory(self, tmp_path, logger):
        assert load_records(str(tmp_path / "absent"), logger=logger) == []

    def test_orders_records_by_issue_date(self, tmp_path, logger):
        out = tmp_path / "output"
        self.write_pair(out, "b", build_fa3_xml(issue_date="2026-06-26", invoice_number="second"))
        self.write_pair(out, "a", build_fa3_xml(issue_date="2026-06-24", invoice_number="first"))

        records = load_records(str(tmp_path), logger=logger)

        assert [r.document_number for r in records] == ["first", "second"]


class TestFilterByMonth:
    def make(self, issue_date):
        return parse_invoice_xml(build_fa3_xml(issue_date=issue_date))

    def test_keeps_only_the_requested_month(self):
        records = [self.make("2026-06-25"), self.make("2026-07-01"), self.make("2026-05-31")]
        assert len(filter_by_month(records, "2026-06")) == 1

    def test_none_keeps_everything(self):
        records = [self.make("2026-06-25"), self.make("2026-07-01")]
        assert len(filter_by_month(records, None)) == 2


class TestBuildWorkbook:
    def test_first_row_is_the_header(self):
        records = [parse_invoice_xml(build_fa3_xml())]
        sheet = build_workbook(records).active

        assert [c.value for c in sheet[1]] == COLUMN_HEADERS

    def test_writes_one_row_per_record(self):
        records = [
            parse_invoice_xml(build_fa3_xml(invoice_number="FS 1")),
            parse_invoice_xml(build_fa3_xml(invoice_number="FS 2")),
        ]
        sheet = build_workbook(records).active

        assert sheet.max_row == 3

    def test_money_cells_are_numeric_so_the_column_can_be_summed(self):
        records = [parse_invoice_xml(build_fa3_xml(positions=default_positions()))]
        sheet = build_workbook(records).active

        netto, vat, brutto = sheet.cell(2, 6), sheet.cell(2, 7), sheet.cell(2, 8)
        for cell in (netto, vat, brutto):
            assert isinstance(cell.value, (int, float)), f"{cell.value!r} is not numeric"
        assert netto.value == pytest.approx(200.00)
        assert vat.value == pytest.approx(46.00)
        assert brutto.value == pytest.approx(246.00)

    def test_date_is_written_in_polish_display_order(self):
        records = [parse_invoice_xml(build_fa3_xml(issue_date="2026-06-25"))]
        sheet = build_workbook(records).active
        assert sheet.cell(2, 5).value == "25.06.2026"

    def test_description_column_is_empty_without_the_ai_step(self):
        records = [parse_invoice_xml(build_fa3_xml())]
        sheet = build_workbook(records).active
        assert sheet.cell(2, 10).value in (None, "")

    def test_pozycje_cell_wraps_and_holds_every_item(self):
        records = [parse_invoice_xml(build_fa3_xml(positions=default_positions()))]
        sheet = build_workbook(records).active

        cell = sheet.cell(2, 12)
        assert "1. Usluga hostingu" in cell.value
        assert "2. Domena .pl" in cell.value
        assert cell.alignment.wrap_text is True

    def test_filename_column_carries_the_local_name(self, tmp_path, logger):
        out = tmp_path / "output"
        out.mkdir()
        stem = "2026-06-25, Dostawca, FS 1, ksef"
        (out / f"{stem}.xml").write_bytes(build_fa3_xml())
        (out / f"{stem}.pdf").write_bytes(b"%PDF")

        records = load_records(str(tmp_path), logger=logger)
        sheet = build_workbook(records).active

        assert sheet.cell(2, 11).value == f"{stem}.pdf"

    def test_empty_record_list_still_produces_a_header(self):
        sheet = build_workbook([]).active
        assert [c.value for c in sheet[1]] == COLUMN_HEADERS
        assert sheet.max_row == 1


class TestWriteReport:
    def test_writes_a_workbook_that_reads_back(self, tmp_path):
        records = [parse_invoice_xml(build_fa3_xml(positions=default_positions()))]
        target = tmp_path / "nested" / "ksef-2026-06.xlsx"

        write_report(records, str(target))

        assert target.exists()
        sheet = load_workbook(str(target)).active
        assert [c.value for c in sheet[1]] == COLUMN_HEADERS
        assert sheet.cell(2, 6).value == pytest.approx(200.00)


class TestDescribeInvoices:
    def test_fills_the_description_column(self, logger):
        records = [parse_invoice_xml(build_fa3_xml())]
        client = StubClient(replies=["usluga hostingu na potrzeby dzialalnosci"])

        describe_invoices(records, client, logger)

        assert records[0].description == "usluga hostingu na potrzeby dzialalnosci"

    def test_sends_the_counterparty_positions_and_totals(self, logger):
        records = [parse_invoice_xml(build_fa3_xml(positions=default_positions()))]
        client = StubClient(replies=["opis"])

        describe_invoices(records, client, logger)

        prompt = client.messages.calls[0]["messages"][0]["content"]
        assert SELLER_NAME in prompt
        assert "Usluga hostingu" in prompt
        assert "200.00" in prompt

    def test_does_not_send_the_raw_xml(self, logger):
        """Only the fields the task needs should leave the machine."""
        records = [parse_invoice_xml(build_fa3_xml())]
        client = StubClient(replies=["opis"])

        describe_invoices(records, client, logger)

        prompt = client.messages.calls[0]["messages"][0]["content"]
        assert "<Faktura" not in prompt
        assert "NumerKSeFDokumentu" not in prompt

    def test_one_failure_leaves_that_cell_empty_and_continues(self, logger):
        records = [
            parse_invoice_xml(build_fa3_xml(invoice_number="FS 1")),
            parse_invoice_xml(build_fa3_xml(invoice_number="FS 2")),
        ]
        client = StubClient(error=RuntimeError("api unavailable"))

        describe_invoices(records, client, logger)

        assert all(r.description == "" for r in records)
        assert len(client.messages.calls) == 2, "a failure must not abort the remaining rows"

    def test_strips_whitespace_from_the_reply(self, logger):
        records = [parse_invoice_xml(build_fa3_xml())]
        client = StubClient(replies=["  opis z bialymi znakami  \n"])

        describe_invoices(records, client, logger)

        assert records[0].description == "opis z bialymi znakami"

    def test_uses_the_project_sonnet_model(self, logger):
        records = [parse_invoice_xml(build_fa3_xml())]
        client = StubClient(replies=["opis"])

        describe_invoices(records, client, logger)

        assert client.messages.calls[0]["model"] == ksef_excel.CLAUDE_SONNET_MODEL

    def test_no_records_makes_no_calls(self, logger):
        client = StubClient()
        describe_invoices([], client, logger)
        assert client.messages.calls == []
