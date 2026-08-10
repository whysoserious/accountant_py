#!/usr/bin/env python3
"""
Main accountant script for downloading and renaming invoices.
"""

import argparse
import sys
import os
from typing import List
from config_parser import load_config, validate_mailbox_names
from imap_client import IMAPClient
from attachment_processor import AttachmentProcessor, ProcessResult
from invoice_renamer import InvoiceRenamer
from ksef_client import KSeFClient, KSeFConfig as KSeFClientConfig
from ksef_excel import describe_invoices, filter_by_month, load_records, write_report
from logger_util import setup_logger, create_download_report


def _previous_month(today=None) -> str:
    """
    Return the previous calendar month as YYYY-MM.

    Args:
        today: Reference date; defaults to the current date. Injectable so the
            December-to-January rollover can be tested without freezing time.
    """
    from datetime import date

    today = today or date.today()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{year:04d}-{month:02d}"


def excel_command(args: argparse.Namespace) -> int:
    """
    Build the accountant's Excel report from locally saved KSeF invoices.

    Reads the FA(3) XML sidecars already on disk rather than querying KSeF, so
    the command is offline, idempotent, and able to regenerate any past month.

    Args:
        args: Parsed command line arguments

    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        config = load_config(args.config)

        logger = setup_logger(
            name="accountant_excel",
            log_file=config.output.log_file,
            level=args.log_level,
        )

        month = args.month or _previous_month()
        directory = args.directory or config.output.main_directory

        logger.info(f"Building Excel report for {month} from {directory}")

        records = filter_by_month(load_records(directory, role=args.role, logger=logger), month)

        if not records:
            # An empty month is not an error -- there may simply be no invoices.
            logger.warning(f"No invoices found for {month}; no report written.")
            return 0

        logger.info(f"{len(records)} invoice(s) in scope for {month}")

        if args.no_ai:
            logger.info("Skipping description generation (--no-ai).")
        else:
            import anthropic

            client = anthropic.Anthropic(api_key=config.api_key)
            logger.info("Generating invoice descriptions...")
            describe_invoices(records, client, logger)

        output_path = args.output or os.path.join(directory, "output", f"ksef-{month}.xlsx")
        write_report(records, output_path)
        logger.info(f"Report written to: {output_path}")

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Could not write the report: {e}", file=sys.stderr)
        return 1


def download_command(args: argparse.Namespace) -> int:
    """
    Execute the download command to fetch invoices from email.

    Args:
        args: Parsed command line arguments

    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        # Load configuration
        config = load_config(args.config)

        # Set up logger
        logger = setup_logger(
            name="accountant",
            log_file=config.output.log_file,
            level=args.log_level,
        )

        logger.info("Starting invoice download process...")
        logger.info(f"Configuration file: {args.config}")

        # Validate mailboxes
        mailbox_names = validate_mailbox_names(
            config,
            args.mailboxes if not args.all_mailboxes else None,
        )

        logger.info(f"Mailboxes to process: {', '.join(mailbox_names)}")

        # Create output directories
        os.makedirs(config.output.main_directory, exist_ok=True)
        os.makedirs(config.output.uncertain_directory, exist_ok=True)

        # Initialize attachment processor
        processor = AttachmentProcessor(
            api_key=config.api_key,
            nip=config.nip,
            nip_check_prompt=config.prompts.nip_check,
            logger=logger,
            blacklist_keywords=config.filter.blacklist_keywords,
        )

        # Process each mailbox
        mailbox_results = {}
        all_process_results: List[ProcessResult] = []

        for mailbox_name in mailbox_names:
            mailbox_config = config.mailboxes[mailbox_name]

            logger.info(f"\n{'='*70}")
            logger.info(f"Processing mailbox: {mailbox_name}")
            logger.info(f"{'='*70}")

            # Create IMAP client
            imap_client = IMAPClient(mailbox_config, logger)

            try:
                # Connect to mailbox
                try:
                    imap_client.connect()
                except Exception as e:
                    logger.error(f"Failed to connect to mailbox {mailbox_name}: {e}")
                    logger.warning(f"Skipping mailbox {mailbox_name} and continuing with others...")
                    continue

                # Search and extract attachments
                search_result = imap_client.search_and_extract_attachments(config.search)
                mailbox_results[mailbox_name] = search_result

                # Process each attachment
                logger.info(f"\nProcessing {len(search_result.attachments_found)} attachments...")

                for attachment in search_result.attachments_found:
                    result = processor.process_attachment(
                        attachment,
                        config.output.main_directory,
                        config.output.uncertain_directory,
                    )
                    all_process_results.append(result)

            except Exception as e:
                logger.error(f"Error processing mailbox {mailbox_name}: {e}")
                logger.warning(f"Skipping mailbox {mailbox_name} and continuing with others...")

            finally:
                # Disconnect from mailbox
                imap_client.disconnect()

        # Create download report
        create_download_report(mailbox_results, all_process_results, config.output.log_file)

        logger.info("Download process completed successfully!")
        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("\nMake sure your config file exists. See config.example.yaml for reference.")
        return 1
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


def ksef_command(args: argparse.Namespace) -> int:
    """
    Execute the ksef command to download invoices from KSeF.

    Args:
        args: Parsed command line arguments

    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        config = load_config(args.config)

        logger = setup_logger(
            name="accountant_ksef",
            log_file=config.output.log_file,
            level=args.log_level,
        )

        logger.info("Starting KSeF invoice download...")

        if not config.ksef:
            logger.error("No KSeF configuration found in config file.")
            logger.error("Add a 'ksef' section to your config. See config.example.yaml.")
            return 1

        ksef_config = KSeFClientConfig(
            nip=config.nip,
            token=config.ksef.token,
            environment=config.ksef.environment,
        )

        client = KSeFClient(ksef_config, logger)

        try:
            client.connect()

            role = getattr(args, "role", "buyer")
            month = getattr(args, "month", None)
            use_bulk = getattr(args, "bulk", False)

            if use_bulk:
                invoices = client.bulk_export(role=role, month=month)
            else:
                invoices = client.query_invoices(role=role, month=month)

            if not invoices:
                logger.info("No invoices found in KSeF for the given criteria.")
                return 0

            saved = client.save_invoices(invoices, config.output.main_directory)

            logger.info(
                f"\nKSeF download complete: {len(saved)} invoices "
                f"saved to {config.output.main_directory}"
            )

        finally:
            client.disconnect()

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (ConnectionError, PermissionError) as e:
        print(f"KSeF connection error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


def rename_command(args: argparse.Namespace) -> int:
    """
    Execute the rename command to rename invoice files.

    Args:
        args: Parsed command line arguments

    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        # Load configuration
        config = load_config(args.config)

        # Set up logger
        logger = setup_logger(
            name="accountant_rename",
            log_file=None,  # No log file for rename
            level=args.log_level,
        )

        logger.info("Starting invoice renaming process...")
        logger.info(f"Configuration file: {args.config}")

        # Initialize renamer
        renamer = InvoiceRenamer(
            api_key=config.api_key,
            categorization_prompt=config.prompts.categorization,
            logger=logger,
        )

        # Process files or directory
        if args.files:
            # Process specific files
            logger.info(f"Processing {len(args.files)} file(s)...")
            processed_count = 0
            error_count = 0

            for file_path in args.files:
                try:
                    if renamer.process_invoice_file(file_path):
                        processed_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    logger.error(f"Error processing file '{file_path}': {e}")
                    error_count += 1

        elif args.directory:
            # Process directory
            logger.info(f"Processing directory: {args.directory}")
            processed_count, error_count = renamer.process_directory(args.directory)

        else:
            logger.error("No files or directory specified!")
            return 1

        # Summary
        logger.info(f"\nSummary: Processed {processed_count} files with {error_count} errors.")

        return 0 if error_count == 0 else 1

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


def main() -> None:
    """Main entry point for the accountant script."""
    parser = argparse.ArgumentParser(
        description="Accountant script for managing invoices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download invoices from all configured mailboxes
  python3 accountant.py download --config config.yaml --all-mailboxes

  # Download from specific mailboxes
  python3 accountant.py download --config config.yaml --mailboxes gmail_personal

  # Rename files in a directory
  python3 accountant.py rename --config config.yaml --directory ./invoices

  # Rename specific files
  python3 accountant.py rename --config config.yaml --files invoice1.pdf invoice2.pdf

  # Build the accountant's Excel report for a month
  python3 accountant.py excel --month 2026-06

  # Build it without spending API calls on descriptions
  python3 accountant.py excel --month 2026-06 --no-ai
        """,
    )

    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Download command
    download_parser = subparsers.add_parser("download", help="Download invoices from email")
    download_group = download_parser.add_mutually_exclusive_group(required=True)
    download_group.add_argument(
        "--mailboxes",
        nargs="+",
        help="List of mailbox names to process (as defined in config)",
    )
    download_group.add_argument(
        "--all-mailboxes",
        action="store_true",
        help="Process all configured mailboxes",
    )

    # KSeF command
    ksef_parser = subparsers.add_parser("ksef", help="Download invoices from KSeF")
    ksef_parser.add_argument(
        "--role",
        choices=["buyer", "seller"],
        default="buyer",
        help="Invoice role: buyer (purchase) or seller (sales) (default: buyer)",
    )
    ksef_parser.add_argument(
        "--month",
        type=str,
        default=None,
        help="Month to download invoices for in YYYY-MM format (default: current month)",
    )
    ksef_parser.add_argument(
        "--bulk",
        action="store_true",
        help=(
            "Use the async bulk export endpoint instead of per-invoice downloads. "
            "Bypasses the per-invoice rate limit at the cost of a one-time "
            "schedule/poll wait."
        ),
    )

    # Rename command
    rename_parser = subparsers.add_parser("rename", help="Rename invoice files based on content")
    rename_group = rename_parser.add_mutually_exclusive_group(required=True)
    rename_group.add_argument("--files", nargs="+", help="List of PDF files to rename")
    rename_group.add_argument("--directory", "-d", help="Directory containing PDF files to rename")

    # Excel command
    excel_parser = subparsers.add_parser(
        "excel",
        help="Build the accountant's Excel report from downloaded KSeF invoices",
    )
    excel_parser.add_argument(
        "--month",
        type=str,
        default=None,
        help="Month to report on in YYYY-MM format (default: previous month)",
    )
    excel_parser.add_argument(
        "--directory",
        "-d",
        default=None,
        help="Directory to scan for invoice XML (default: output.main_directory from config)",
    )
    excel_parser.add_argument(
        "--role",
        choices=["buyer", "seller"],
        default="buyer",
        help="Whose counterparty to report: buyer reports sellers (default: buyer)",
    )
    excel_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Path for the .xlsx file (default: <directory>/output/ksef-<month>.xlsx)",
    )
    excel_parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Leave the description column empty instead of generating it",
    )

    # Parse arguments
    args = parser.parse_args()

    # Convert log level string to logging constant
    import logging

    log_level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    args.log_level = log_level_map[args.log_level]

    # Execute command
    if args.command == "download":
        exit_code = download_command(args)
    elif args.command == "ksef":
        exit_code = ksef_command(args)
    elif args.command == "rename":
        exit_code = rename_command(args)
    elif args.command == "excel":
        exit_code = excel_command(args)
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
