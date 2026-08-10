#!/usr/bin/env python3
"""
Tests for blacklist filtering.

The blacklist runs before any LLM call, so these tests need no API access. They
build real single-page PDFs, because the filter reads text through PyPDF2 and a
stub would not exercise that path.
"""

import logging
from datetime import datetime

from attachment_processor import AttachmentProcessor
from imap_client import EmailAttachment

from tests.fixtures.synthetic import build_text_pdf


def make_attachment(
    content: bytes,
    filename: str = "invoice.pdf",
    content_type: str = "application/pdf",
) -> EmailAttachment:
    return EmailAttachment(
        filename=filename,
        content=content,
        content_type=content_type,
        email_subject="Faktura",
        email_from="sender@example.com",
        email_date=datetime(2026, 6, 25),
    )


def make_processor(logger: logging.Logger, keywords) -> AttachmentProcessor:
    return AttachmentProcessor(
        api_key="test-key-not-a-real-credential",
        nip="0000000000",
        nip_check_prompt="does this contain {nip}",
        logger=logger,
        blacklist_keywords=keywords,
    )


class TestBlacklistDisabled:
    def test_no_keywords_configured_never_filters(self, processor):
        attachment = make_attachment(build_text_pdf("REKLAMA promocja"))
        assert processor._is_blacklisted(attachment) is False

    def test_empty_keyword_list_never_filters(self, logger):
        processor = make_processor(logger, [])
        attachment = make_attachment(build_text_pdf("REKLAMA promocja"))
        assert processor._is_blacklisted(attachment) is False


class TestBlacklistMatching:
    def test_matches_a_keyword_present_in_the_text(self, logger):
        processor = make_processor(logger, ["reklama"])
        attachment = make_attachment(build_text_pdf("Oferta reklama specjalna"))
        assert processor._is_blacklisted(attachment) is True

    def test_matching_is_case_insensitive_both_ways(self, logger):
        processor = make_processor(logger, ["REKLAMA"])
        attachment = make_attachment(build_text_pdf("oferta reklama"))
        assert processor._is_blacklisted(attachment) is True

    def test_passes_documents_without_any_keyword(self, logger):
        processor = make_processor(logger, ["reklama", "newsletter"])
        attachment = make_attachment(build_text_pdf("Faktura za usluge hostingu"))
        assert processor._is_blacklisted(attachment) is False

    def test_any_one_keyword_is_enough(self, logger):
        processor = make_processor(logger, ["nieobecne", "newsletter"])
        attachment = make_attachment(build_text_pdf("Nasz newsletter miesieczny"))
        assert processor._is_blacklisted(attachment) is True

    def test_matches_a_substring_within_a_word(self, logger):
        """Documents current behaviour: matching is substring-based, not word-based."""
        processor = make_processor(logger, ["reklam"])
        attachment = make_attachment(build_text_pdf("materialy reklamowe"))
        assert processor._is_blacklisted(attachment) is True


class TestNonPdfAndFailureHandling:
    def test_non_pdf_attachment_is_not_filtered(self, logger):
        processor = make_processor(logger, ["reklama"])
        attachment = make_attachment(
            b"reklama in plain text",
            filename="notes.txt",
            content_type="text/plain",
        )
        assert processor._is_blacklisted(attachment) is False

    def test_pdf_detected_by_extension_when_content_type_is_generic(self, logger):
        processor = make_processor(logger, ["reklama"])
        attachment = make_attachment(
            build_text_pdf("reklama"),
            filename="scan.PDF",
            content_type="application/octet-stream",
        )
        assert processor._is_blacklisted(attachment) is True

    def test_unreadable_pdf_fails_open_rather_than_discarding(self, logger):
        """
        A corrupt file must not be silently dropped: an unparseable attachment
        might be a real invoice, so the filter declines to blacklist it.
        """
        processor = make_processor(logger, ["reklama"])
        attachment = make_attachment(b"%PDF-1.4 truncated garbage")
        assert processor._is_blacklisted(attachment) is False
