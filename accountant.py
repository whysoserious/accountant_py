#!/usr/bin/env python3
"""
Main accountant script for downloading and renaming invoices.
"""
import argparse
import sys
import os
from typing import List, Optional
from config_parser import load_config, validate_mailbox_names
from imap_client import IMAPClient
from attachment_processor import AttachmentProcessor, ProcessResult
from invoice_renamer import InvoiceRenamer
from logger_util import setup_logger, create_download_report


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
                    logger.error(
                        f"Failed to connect to mailbox {mailbox_name}: {e}"
                    )
                    logger.warning(
                        f"Skipping mailbox {mailbox_name} and continuing with others..."
                    )
                    continue

                # Search and extract attachments
                search_result = imap_client.search_and_extract_attachments(
                    config.search
                )
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
                logger.warning(
                    f"Skipping mailbox {mailbox_name} and continuing with others..."
                )

            finally:
                # Disconnect from mailbox
                imap_client.disconnect()

        # Create download report
        create_download_report(
            mailbox_results, all_process_results, config.output.log_file
        )

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
    download_parser = subparsers.add_parser(
        "download", help="Download invoices from email"
    )
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

    # Rename command
    rename_parser = subparsers.add_parser(
        "rename", help="Rename invoice files based on content"
    )
    rename_group = rename_parser.add_mutually_exclusive_group(required=True)
    rename_group.add_argument(
        "--files", nargs="+", help="List of PDF files to rename"
    )
    rename_group.add_argument(
        "--directory", "-d", help="Directory containing PDF files to rename"
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
    elif args.command == "rename":
        exit_code = rename_command(args)
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
