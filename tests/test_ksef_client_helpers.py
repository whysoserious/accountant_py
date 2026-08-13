#!/usr/bin/env python3
"""Tests for the pure helpers on KSeFClient. No network access."""

import os

from ksef_client import KSeFClient

from ksef_excel import MANIFEST_FILENAME

from tests.fixtures.synthetic import BUYER_NIP, SELLER_NIP, build_fa3_xml

KSEF_NUMBER = "1000000000-20260625-AAAAAAAAAAAA-01"


class TestExtractKsefNumber:
    """The KSeF reference number is parsed straight out of the FA(3) bytes."""

    def test_reads_bare_tag(self):
        xml = build_fa3_xml(ksef_number=KSEF_NUMBER, include_ksef_number=True)
        assert KSeFClient._extract_ksef_number_from_xml(xml) == KSEF_NUMBER

    def test_reads_namespace_prefixed_tag(self):
        xml = build_fa3_xml(ksef_number=KSEF_NUMBER, include_ksef_number=True, prefix="tns:")
        assert KSeFClient._extract_ksef_number_from_xml(xml) == KSEF_NUMBER

    def test_returns_none_for_a_realistic_document(self):
        """
        Real FA(3) documents carry no KSeF reference number, so the default
        fixture has none either. Bulk export relies on the ZIP entry name in
        exactly this case.
        """
        xml = build_fa3_xml()
        assert KSeFClient._extract_ksef_number_from_xml(xml) is None

    def test_tolerates_surrounding_whitespace(self):
        xml = (
            b"<Faktura><NumerKSeFDokumentu>\n  "
            + KSEF_NUMBER.encode()
            + b"\n</NumerKSeFDokumentu></Faktura>"
        )
        assert KSeFClient._extract_ksef_number_from_xml(xml) == KSEF_NUMBER

    def test_ignores_content_past_the_8kb_window(self):
        """
        Documents a real limitation rather than asserting ideal behaviour: only
        the first 8 KiB is decoded, so a reference number pushed beyond that is
        invisible. Worth pinning so the boundary is not moved unknowingly.
        """
        padding = b"<Padding>" + (b"x" * 9000) + b"</Padding>"
        xml = (
            b"<Faktura>"
            + padding
            + b"<NumerKSeFDokumentu>"
            + KSEF_NUMBER.encode()
            + b"</NumerKSeFDokumentu></Faktura>"
        )
        assert KSeFClient._extract_ksef_number_from_xml(xml) is None

    def test_survives_undecodable_bytes(self):
        xml = (
            b"\xff\xfe<Faktura><NumerKSeFDokumentu>"
            + KSEF_NUMBER.encode()
            + b"</NumerKSeFDokumentu></Faktura>"
        )
        assert KSeFClient._extract_ksef_number_from_xml(xml) == KSEF_NUMBER


class TestSanitize:
    """Filename sanitisation for values that arrive from KSeF metadata."""

    def test_replaces_every_invalid_character(self):
        assert KSeFClient._sanitize('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"

    def test_leaves_safe_text_untouched(self):
        assert KSeFClient._sanitize("FS 0001/2026") == "FS 0001_2026"

    def test_truncates_to_100_characters(self):
        assert len(KSeFClient._sanitize("x" * 250)) == 100

    def test_handles_empty_string(self):
        assert KSeFClient._sanitize("") == ""


class TestUniquePath:
    """Collision handling must never overwrite an already-saved invoice."""

    def test_returns_original_when_free(self, tmp_path):
        target = str(tmp_path / "invoice.xml")
        assert KSeFClient._unique_path(target) == target

    def test_appends_counter_when_taken(self, tmp_path):
        target = tmp_path / "invoice.xml"
        target.write_bytes(b"first")
        assert KSeFClient._unique_path(str(target)) == str(tmp_path / "invoice_1.xml")

    def test_skips_over_existing_counters(self, tmp_path):
        (tmp_path / "invoice.xml").write_bytes(b"first")
        (tmp_path / "invoice_1.xml").write_bytes(b"second")
        (tmp_path / "invoice_2.xml").write_bytes(b"third")
        assert KSeFClient._unique_path(str(tmp_path / "invoice.xml")) == str(
            tmp_path / "invoice_3.xml"
        )

    def test_preserves_multi_dot_extension_stem(self, tmp_path):
        target = tmp_path / "2026-06-25, Firma, FS 1.2026, ksef.pdf"
        target.write_bytes(b"first")
        result = KSeFClient._unique_path(str(target))
        assert os.path.basename(result) == "2026-06-25, Firma, FS 1.2026, ksef_1.pdf"


class TestSaveIsIdempotent:
    """
    Re-running a month must record KSeF numbers without saving another copy of
    every invoice. The previous behaviour appended _1, _2, ... which is exactly
    how duplicate rows reached the report.
    """

    def client(self, logger):
        from ksef_client import KSeFClient, KSeFConfig

        return KSeFClient(KSeFConfig(nip=SELLER_NIP, token="not-a-real-token"), logger)

    def invoice(self, xml, number="FS 1/2026", ksef="1000000006-20260625-A1B2C3D4E5F6-7A"):
        from ksef_client import KSeFInvoice

        return KSeFInvoice(
            ksef_number=ksef,
            invoice_number=number,
            issue_date="2026-06-25",
            seller_name="Przykladowy Dostawca",
            seller_nip=SELLER_NIP,
            buyer_name="Nabywca",
            buyer_nip=BUYER_NIP,
            net_amount=200.0,
            gross_amount=246.0,
            currency="PLN",
            xml_content=xml,
            invoice_type="VAT",
        )

    def test_first_save_writes_the_files(self, tmp_path, logger):
        client = self.client(logger)
        client.save_invoices([self.invoice(build_fa3_xml())], str(tmp_path))

        xmls = list(tmp_path.glob("**/*.xml"))
        assert len([p for p in xmls if p.name != MANIFEST_FILENAME]) == 1

    def test_second_save_of_the_same_invoice_adds_no_duplicate(self, tmp_path, logger):
        client = self.client(logger)
        xml = build_fa3_xml()
        client.save_invoices([self.invoice(xml)], str(tmp_path))
        client.save_invoices([self.invoice(xml)], str(tmp_path))

        invoice_xmls = [p for p in tmp_path.glob("**/*.xml") if p.name != MANIFEST_FILENAME]
        assert len(invoice_xmls) == 1, f"re-run duplicated files: {[p.name for p in invoice_xmls]}"
        assert not list(tmp_path.glob("**/*_1.xml"))

    def test_the_rerun_still_records_the_ksef_number(self, tmp_path, logger):
        """The whole point of re-running an older month."""
        client = self.client(logger)
        xml = build_fa3_xml()
        client.save_invoices([self.invoice(xml, ksef="")], str(tmp_path))
        assert not list(tmp_path.glob("**/" + MANIFEST_FILENAME))

        number = "1000000006-20260625-A1B2C3D4E5F6-7A"
        client.save_invoices([self.invoice(xml, ksef=number)], str(tmp_path))

        manifests = list(tmp_path.glob("**/" + MANIFEST_FILENAME))
        assert manifests, "a re-run must write the manifest even when files exist"
        assert number in manifests[0].read_text(encoding="utf-8")

    def test_a_genuinely_new_invoice_is_still_saved(self, tmp_path, logger):
        client = self.client(logger)
        client.save_invoices([self.invoice(build_fa3_xml(invoice_number="FS 1"))], str(tmp_path))
        client.save_invoices(
            [self.invoice(build_fa3_xml(invoice_number="FS 2"), number="FS 2")], str(tmp_path)
        )

        invoice_xmls = [p for p in tmp_path.glob("**/*.xml") if p.name != MANIFEST_FILENAME]
        assert len(invoice_xmls) == 2
