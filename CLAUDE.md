# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands
- Environment setup: `python3 -m venv venv && source venv/bin/activate && pip3 install -r requirements.txt`
- Download invoices: `python3 accountant.py download --config config.yaml --all-mailboxes`
- Upload to QNAP: `python3 accountant.py upload --config config.yaml --directory ./invoices`
- Rename invoices: `python3 accountant.py rename --config config.yaml --directory ./invoices`
- Compile check: `python3 -m py_compile accountant.py && python3 -m py_compile qnap_client.py && echo "OK"`
- Format code: `black *.py`
- Lint code: `flake8 *.py`

## Architecture
- `accountant.py` - Main entry point with CLI subcommands (download, upload, rename)
- `config_parser.py` - YAML config parsing into dataclasses
- `imap_client.py` - IMAP email connection and attachment extraction
- `attachment_processor.py` - Claude API-based NIP checking for attachments
- `invoice_renamer.py` - Claude API-based invoice analysis and renaming
- `qnap_client.py` - QNAP NAS File Station API client for uploading invoices
- `logger_util.py` - Logging setup and download report generation
- `constants.py` - Shared constants (models, file types, limits)

## QNAP Integration
- Auth uses base64-encoded password via `/cgi-bin/authLogin.cgi` with `serviceKey=1`
- File operations via `/cgi-bin/filemanager/utilRequest.cgi`
- Upload verification is required: the API may return success even when writes fail
- Folder structure: `BUFOR/DOKUMENTY KSIĘGOWE - ZAKUP/MM.YYYY/` for purchase invoices
- Invoice filename convention: `YYYY-MM-DD, Company, InvoiceNumber, Description.pdf`

## Code Style Guidelines
- Use PEP 8 style guidelines for Python code
- Imports order: standard library, third-party, local
- Use type hints for function parameters and return values
- Use descriptive variable names in English
- All log messages and comments should be in English
- Handle exceptions with specific exception types
- All comments, log messages, and error messages should be in English
- Maintain 4-space indentation
- Line length max: 100 characters
- Use docstrings for functions and classes
- Maintain consistent error handling approach using try/except blocks