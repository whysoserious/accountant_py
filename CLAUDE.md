# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands
- Environment setup: `uv sync` (do not use pip; dependencies live in `pyproject.toml` and are locked in `uv.lock`)
- Add a dependency: `uv add <pkg>` — never edit `pyproject.toml` deps by hand without re-running `uv lock`
- Download invoices from email: `uv run python accountant.py download --all-mailboxes`
- Download invoices from KSeF: `uv run python accountant.py ksef --role buyer --month 2026-03`
- Rename invoices: `uv run python accountant.py rename --directory ./invoices`
- Build accountant Excel report: `uv run python accountant.py excel --month 2026-06`
- Repair missing invoice PDFs: `uv run python accountant.py render` (offline, no KSeF query)
- **Whole monthly routine in one go**: `uv run python accountant.py all --month 2026-06`
- Compile check: `uv run python -m py_compile accountant.py && echo "OK"`
- Run tests: `uv run pytest`
- Format code: `uv run black .`
- Lint code: `uv run flake8 .`

CI runs `uv run black --check`, `uv run flake8`, then `uv run pytest` on every
pull request. Run all three locally before pushing; the lint job gates the test
matrix (Python 3.12–3.14; 3.12 is the floor because `ksef2` requires it).

## Architecture
- `accountant.py` - Main entry point with CLI subcommands (download, ksef, rename, excel,
  render, all)
- `config_parser.py` - YAML config parsing into dataclasses
- `imap_client.py` - IMAP email connection and attachment extraction
- `attachment_processor.py` - Claude API-based NIP checking, blacklist filtering
- `invoice_renamer.py` - Claude API-based invoice analysis, renaming, deduplication
- `ksef_client.py` - KSeF API client for downloading invoices (uses ksef2 library)
- `ksef_pdf_renderer.py` - Renders KSeF FA(3) XML invoices to PDF using fpdf2
- `ksef_excel.py` - Builds the accountant's monthly .xlsx from local FA(3) XML;
  also renders missing PDFs from local XML (`render` subcommand)
- `tests/` - pytest suite; `tests/fixtures/synthetic.py` builds all test data
- `logger_util.py` - Logging setup and download report generation
- `constants.py` - Shared constants (models, file types, limits)

## Key Conventions
- Invoice filename format: `YYYY-MM-DD, Company, InvoiceNumber, Description.pdf`
- KSeF invoices saved as both `.pdf` and `.xml` with `ksef` as description
- Output organized by month: `invoices/YYYY-MM/`
- Deduplication by invoice number (from filename) and file checksum (SHA256)
- Blacklist filtering: PDFs matching keywords in `filter.blacklist_keywords` are skipped before LLM
- Claude models: claude-sonnet-4-6 (NIP check, categorization), claude-haiku-4-5 (NIP extraction)
- KSeF auth: token-based via ksef2 library, XML→PDF rendering done locally
- Excel report: 12 columns (the accountant's 10, then `Nazwa pliku` and `pozycje`).
  Built from local XML only — never queries KSeF. `--no-ai` skips description generation.
- Description column uses a **fixed taxonomy** from `excel.categories`, so the same
  expense gets the same label every month. An off-list model reply is kept but warned
  about — add the category rather than let wording drift.
- `excel.non_deductible_keywords` marks expenses that cannot be deducted (multisport,
  food, supplements, toys). Matching rows are **kept and highlighted red**, never
  dropped. Keyword matching runs before any API call and ignores case and Polish
  diacritics (including `ł`, which NFKD does not decompose).
- `load_records` **deduplicates by invoice identity**. The same invoice legitimately
  lands on disk twice (`..., ksef.pdf` plus a separately renamed copy); reporting both
  double-counts the cost. Duplicates are dropped (they carry no information) and logged.
- **The KSeF number is not in the FA(3) XML.** It is assigned by KSeF and returned in
  metadata, so `ksef` records it in a `ksef-numbers.json` manifest per month, keyed by
  invoice identity (seller NIP + invoice number + issue date) so `rename` cannot orphan
  it. `excel` resolves it from the XML, then the manifest, then the rendered PDF's text.
- `save_invoices` is **idempotent**: an invoice already on disk (matched on identity, not
  filename) is not written again, but its KSeF number is still recorded. Re-running a
  month to backfill numbers is therefore safe and does not create duplicates.
- `all` chains ksef → rename → render → excel. It resolves the month **once** and passes
  it to every step: `ksef` alone defaults to the current month while `excel` defaults to
  the previous one, so a pipeline trusting those defaults would download one month and
  report another. Steps stop at the first failure, since each depends on the previous.
- **No real invoice data in the repository.** Test fixtures use invented names and
  synthetic checksum-valid NIPs. Generated `.xlsx` files are gitignored.

## Code Style Guidelines
- Use PEP 8 style guidelines for Python code
- Imports order: standard library, third-party, local
- Use type hints for function parameters and return values
- Use descriptive variable names in English
- All log messages and comments should be in English
- Handle exceptions with specific exception types (never bare `except:`)
- Maintain 4-space indentation
- Line length max: 100 characters
- Use docstrings for functions and classes
- Format with `black` before committing
