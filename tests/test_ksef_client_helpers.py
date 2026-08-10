#!/usr/bin/env python3
"""Tests for the pure helpers on KSeFClient. No network access."""

import os

from ksef_client import KSeFClient

from tests.fixtures.synthetic import build_fa3_xml

KSEF_NUMBER = "1000000000-20260625-AAAAAAAAAAAA-01"


class TestExtractKsefNumber:
    """The KSeF reference number is parsed straight out of the FA(3) bytes."""

    def test_reads_bare_tag(self):
        xml = build_fa3_xml(ksef_number=KSEF_NUMBER)
        assert KSeFClient._extract_ksef_number_from_xml(xml) == KSEF_NUMBER

    def test_reads_namespace_prefixed_tag(self):
        xml = build_fa3_xml(ksef_number=KSEF_NUMBER, prefix="tns:")
        assert KSeFClient._extract_ksef_number_from_xml(xml) == KSEF_NUMBER

    def test_returns_none_when_element_absent(self):
        xml = build_fa3_xml(include_ksef_number=False)
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
