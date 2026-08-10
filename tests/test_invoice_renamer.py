#!/usr/bin/env python3
"""Tests for InvoiceRenamer's filename and deduplication helpers. No network access."""

import hashlib
import os

from constants import MAX_FILENAME_LENGTH


class TestSanitizeFilename:
    def test_replaces_every_invalid_character(self, renamer):
        assert renamer.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"

    def test_keeps_commas_and_spaces_used_by_the_naming_convention(self, renamer):
        assert renamer.sanitize_filename("2026-06-25, Firma") == "2026-06-25, Firma"

    def test_truncates_leaving_room_for_counter_and_extension(self, renamer):
        result = renamer.sanitize_filename("x" * 400)
        assert len(result) == MAX_FILENAME_LENGTH - 20


class TestGenerateNewFilename:
    def test_joins_the_four_components_in_convention_order(self, renamer):
        data = {
            "date": "2026-06-25",
            "company": "Przykladowy Dostawca",
            "invoice_number": "FS 1/2026",
            "description": "usluga hostingu",
        }
        result = renamer.generate_new_filename(data, ".pdf")
        assert result == "2026-06-25, Przykladowy Dostawca, FS 1_2026, usluga hostingu.pdf"

    def test_substitutes_unknown_for_missing_fields(self, renamer):
        result = renamer.generate_new_filename({"date": "2026-06-25"}, ".pdf")
        assert result == "2026-06-25, Unknown, Unknown, Unknown.pdf"

    def test_preserves_the_supplied_extension(self, renamer):
        result = renamer.generate_new_filename({}, ".xml")
        assert result.endswith(".xml")


class TestDedupKey:
    def test_normalises_case_and_surrounding_whitespace(self, renamer):
        assert renamer._make_dedup_key("  Firma SP. Z O.O. ", " FS 1/2026 ") == (
            "firma sp. z o.o.",
            "fs 1/2026",
        )

    def test_returns_none_when_company_missing(self, renamer):
        assert renamer._make_dedup_key(None, "FS 1/2026") is None
        assert renamer._make_dedup_key("", "FS 1/2026") is None

    def test_returns_none_when_invoice_number_missing(self, renamer):
        assert renamer._make_dedup_key("Firma", None) is None
        assert renamer._make_dedup_key("Firma", "") is None

    def test_returns_none_for_unknown_placeholders(self, renamer):
        """'Unknown' is the extraction failure marker, so it must never key a match."""
        assert renamer._make_dedup_key("Unknown", "FS 1/2026") is None
        assert renamer._make_dedup_key("Firma", "Unknown") is None


class TestKeyFromFilename:
    def test_extracts_company_and_number_lowercased(self, renamer):
        name = "2026-06-25, Przykladowa Firma, FS 1_2026, hosting.pdf"
        assert renamer._extract_key_from_filename(name) == ("przykladowa firma", "fs 1_2026")

    def test_returns_none_when_too_few_segments(self, renamer):
        assert renamer._extract_key_from_filename("scan001.pdf") is None
        assert renamer._extract_key_from_filename("2026-06-25, Firma.pdf") is None

    def test_works_without_a_description_segment(self, renamer):
        assert renamer._extract_key_from_filename("2026-06-25, Firma, FS 1.pdf") == (
            "firma",
            "fs 1",
        )


class TestRenamedFileDetection:
    def test_recognises_the_date_prefixed_convention(self, renamer):
        assert renamer._is_renamed_file("2026-06-25, Firma, FS 1, hosting.pdf") is True

    def test_rejects_unrenamed_names(self, renamer):
        assert renamer._is_renamed_file("scan001.pdf") is False
        assert renamer._is_renamed_file("2026-6-5, Firma, FS 1.pdf") is False
        assert renamer._is_renamed_file("2026-06-25 Firma.pdf") is False


class TestDescriptionSegment:
    def test_extracts_and_lowercases_the_description(self, renamer):
        name = "2026-06-25, Firma, FS 1, Usluga Hostingu.pdf"
        assert renamer._extract_description_from_filename(name) == "usluga hostingu"

    def test_returns_none_without_a_description_segment(self, renamer):
        assert renamer._extract_description_from_filename("2026-06-25, Firma, FS 1.pdf") is None

    def test_flags_the_ksef_placeholder_as_generic(self, renamer):
        assert renamer._has_generic_description("2026-06-25, Firma, FS 1, ksef.pdf") is True

    def test_does_not_flag_a_real_description(self, renamer):
        assert renamer._has_generic_description("2026-06-25, Firma, FS 1, paliwo.pdf") is False


class TestFileChecksum:
    def test_matches_hashlib_for_the_same_bytes(self, renamer, tmp_path):
        payload = b"synthetic invoice bytes"
        path = tmp_path / "a.pdf"
        path.write_bytes(payload)
        assert renamer._file_checksum(str(path)) == hashlib.sha256(payload).hexdigest()

    def test_differs_for_different_content(self, renamer, tmp_path):
        first = tmp_path / "a.pdf"
        second = tmp_path / "b.pdf"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        assert renamer._file_checksum(str(first)) != renamer._file_checksum(str(second))

    def test_handles_a_file_larger_than_the_read_chunk(self, renamer, tmp_path):
        payload = os.urandom(8192 * 3 + 17)
        path = tmp_path / "big.pdf"
        path.write_bytes(payload)
        assert renamer._file_checksum(str(path)) == hashlib.sha256(payload).hexdigest()


class TestUniqueFilepath:
    def test_returns_original_when_free(self, renamer, tmp_path):
        target = str(tmp_path / "invoice.pdf")
        assert renamer._get_unique_filepath(target) == target

    def test_appends_counter_when_taken(self, renamer, tmp_path):
        target = tmp_path / "invoice.pdf"
        target.write_bytes(b"first")
        assert renamer._get_unique_filepath(str(target)) == str(tmp_path / "invoice_1.pdf")

    def test_skips_over_existing_counters(self, renamer, tmp_path):
        (tmp_path / "invoice.pdf").write_bytes(b"a")
        (tmp_path / "invoice_1.pdf").write_bytes(b"b")
        assert renamer._get_unique_filepath(str(tmp_path / "invoice.pdf")) == str(
            tmp_path / "invoice_2.pdf"
        )


class TestCompanionXmlHandling:
    """The XML sidecar must track its PDF, since the Excel report joins on it."""

    def test_moves_the_sidecar_alongside_the_renamed_pdf(self, renamer, tmp_path):
        source_pdf = tmp_path / "raw.pdf"
        source_xml = tmp_path / "raw.xml"
        source_pdf.write_bytes(b"pdf")
        source_xml.write_bytes(b"<Faktura/>")
        dest_pdf = tmp_path / "2026-06-25, Firma, FS 1, ksef.pdf"

        renamer._move_companion_xml_alongside(str(source_pdf), str(dest_pdf))

        assert (tmp_path / "2026-06-25, Firma, FS 1, ksef.xml").read_bytes() == b"<Faktura/>"
        assert not source_xml.exists()

    def test_is_a_no_op_when_there_is_no_sidecar(self, renamer, tmp_path):
        source_pdf = tmp_path / "raw.pdf"
        source_pdf.write_bytes(b"pdf")
        dest_pdf = tmp_path / "renamed.pdf"

        renamer._move_companion_xml_alongside(str(source_pdf), str(dest_pdf))

        assert not (tmp_path / "renamed.xml").exists()

    def test_moves_pdf_and_sidecar_into_output(self, renamer, tmp_path):
        pdf = tmp_path / "2026-06-25, Firma, FS 1, ksef.pdf"
        xml = tmp_path / "2026-06-25, Firma, FS 1, ksef.xml"
        pdf.write_bytes(b"pdf")
        xml.write_bytes(b"<Faktura/>")
        output_dir = tmp_path / "output"

        moved = renamer._move_pdf_with_companion(str(pdf), str(output_dir))

        assert moved is not None
        assert os.path.basename(moved) == "2026-06-25, Firma, FS 1, ksef.pdf"
        assert (output_dir / "2026-06-25, Firma, FS 1, ksef.pdf").exists()
        assert (output_dir / "2026-06-25, Firma, FS 1, ksef.xml").exists()
        assert not pdf.exists()
        assert not xml.exists()

    def test_does_not_overwrite_an_existing_file_in_output(self, renamer, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "dup.pdf").write_bytes(b"already here")
        pdf = tmp_path / "dup.pdf"
        pdf.write_bytes(b"incoming")

        moved = renamer._move_pdf_with_companion(str(pdf), str(output_dir))

        assert (output_dir / "dup.pdf").read_bytes() == b"already here"
        assert os.path.basename(moved) == "dup_1.pdf"
