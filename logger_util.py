#!/usr/bin/env python3
"""Logging utilities for accountant script."""

import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "accountant",
    log_file: Optional[str] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Set up logger with console and optional file output.

    Args:
        name: Logger name
        log_file: Optional path to log file
        level: Logging level (default: INFO)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Failed to set up file logging to {log_file}: {e}")

    return logger


def create_download_report(mailbox_results: dict, process_results: list, log_file: str) -> None:
    """
    Create a detailed download report.

    Args:
        mailbox_results: Dictionary of mailbox name -> EmailSearchResult
        process_results: List of ProcessResult objects
        log_file: Path to log file where report was written
    """
    print("\n" + "=" * 70)
    print("DOWNLOAD REPORT")
    print("=" * 70)

    # Mailbox statistics
    print("\nMailbox Statistics:")
    print("-" * 70)
    total_emails = 0
    total_attachments = 0

    for mailbox_name, result in mailbox_results.items():
        total_emails += result.total_emails_searched
        total_attachments += len(result.attachments_found)

        print(f"\n{mailbox_name}:")
        print(f"  Emails searched: {result.total_emails_searched}")
        print(f"  Emails with attachments: {result.emails_with_attachments}")
        print(f"  Attachments found: {len(result.attachments_found)}")

    # Processing statistics
    print("\n\nProcessing Statistics:")
    print("-" * 70)

    with_nip = sum(1 for r in process_results if r.contains_nip)
    without_nip = sum(1 for r in process_results if not r.contains_nip)
    errors = sum(1 for r in process_results if r.error)

    print(f"Total attachments processed: {len(process_results)}")
    print(f"Attachments with NIP: {with_nip}")
    print(f"Attachments without NIP (uncertain): {without_nip}")
    print(f"Errors: {errors}")

    # File listing
    if with_nip > 0:
        print("\n\nFiles with confirmed NIP:")
        print("-" * 70)
        for result in process_results:
            if result.contains_nip and result.saved_path:
                print(f"  - {result.saved_path}")

    if without_nip > 0:
        print("\n\nFiles without confirmed NIP (saved to uncertain/):")
        print("-" * 70)
        for result in process_results:
            if not result.contains_nip and result.saved_path:
                print(f"  - {result.saved_path}")

    if errors > 0:
        print("\n\nErrors:")
        print("-" * 70)
        for result in process_results:
            if result.error:
                print(f"  - {result.attachment.filename}: {result.error}")

    print("\n" + "=" * 70)
    print(f"Detailed log saved to: {log_file}")
    print("=" * 70 + "\n")
