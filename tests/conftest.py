#!/usr/bin/env python3
"""Shared pytest fixtures."""

import logging

import pytest

from attachment_processor import AttachmentProcessor
from invoice_renamer import InvoiceRenamer


@pytest.fixture
def logger() -> logging.Logger:
    """A logger that does not write anywhere during tests."""
    log = logging.getLogger("accountant_py.tests")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return log


@pytest.fixture
def renamer(logger: logging.Logger) -> InvoiceRenamer:
    """
    An InvoiceRenamer built with a dummy key.

    The Anthropic client is constructed lazily and makes no request on
    instantiation, so no network access occurs. Tests here only exercise the
    pure filename and deduplication helpers.
    """
    return InvoiceRenamer(
        api_key="test-key-not-a-real-credential",
        categorization_prompt="categorize this",
        logger=logger,
    )


@pytest.fixture
def processor(logger: logging.Logger) -> AttachmentProcessor:
    """An AttachmentProcessor with no blacklist configured."""
    return AttachmentProcessor(
        api_key="test-key-not-a-real-credential",
        nip="0000000000",
        nip_check_prompt="does this contain {nip}",
        logger=logger,
    )
