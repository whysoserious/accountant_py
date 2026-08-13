# Accountant - Polish Invoice Manager

Downloads your Polish invoices from email and KSeF, names them consistently, and
builds the monthly Excel report your accountant asks for.

## Requirements

| Requirement | Why | Notes |
|---|---|---|
| **Python 3.12+** | `ksef2` publishes no wheel below 3.12 | Installed for you by mise |
| **[uv](https://docs.astral.sh/uv/)** | Dependency management | **pip is not used** — dependencies live in `pyproject.toml`, locked in `uv.lock` |
| **[mise](https://mise.jdx.dev)** *(optional)* | Pins the Python and uv versions | `mise.toml` is checked in |
| **poppler** | `rename` rasterises PDF pages for the vision model | Only needed for `rename`; `ksef` and `excel` work without it |
| **Anthropic API key** | NIP detection, categorisation, invoice descriptions | [console.anthropic.com](https://console.anthropic.com) |
| **KSeF token** | `ksef` downloads | See [KSeF Setup](#ksef-setup) below |
| **IMAP credentials** | `download` from mailboxes | See [Gmail Setup](#gmail-setup) below |

Install poppler:

```bash
sudo dnf install poppler-utils     # Fedora / RHEL
sudo apt install poppler-utils     # Debian / Ubuntu
brew install poppler               # macOS
```

## Setup

**1. Install the toolchain.** With mise, one command gets both Python and uv:

```bash
mise trust && mise install
```

Or install uv on its own and use whatever Python 3.12+ you already have:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install dependencies.** This creates `.venv/` from the lockfile — exact
versions, no resolution:

```bash
uv sync
```

**3. Create your config.** `config.yaml` is gitignored; never commit it.

```bash
cp config.example.yaml config.yaml
```

Fill in at minimum:

```yaml
anthropic:
  api_key: "sk-ant-..."   # your key
nip: "1234567890"         # your own 10-digit NIP
```

Then add a `mailboxes:` entry if you want `download`, and a `ksef:` section with
your token if you want `ksef`. Both are optional — the commands you skip
configuring simply cannot run.

**4. Verify the install** before pointing it at anything real. The suite is
offline and makes no API calls:

```bash
uv run pytest        # expect every test to pass (133 of them at time of writing)
```

## First run

Start with KSeF for last month, which needs no mailbox setup and is read-only:

```bash
# 1. Download last month's purchase invoices (XML + rendered PDF)
uv run python accountant.py ksef --role buyer --month 2026-06

# 2. Rename them into <directory>/output/ with a consistent, sortable name
uv run python accountant.py rename --directory ./invoices

# 3. Build the Excel report. Start with --no-ai: no API calls, instant.
uv run python accountant.py excel --month 2026-06 --no-ai

# 4. Happy with the sheet? Re-run without --no-ai to fill in the
#    "Opis faktury" column with a tax-deduction rationale per invoice.
uv run python accountant.py excel --month 2026-06
```

The workbook lands at `./invoices/output/ksef-2026-06.xlsx`.

> **Note on step 2:** `rename` is destructive to its source directory — it
> deletes duplicates it detects. Run it on a copy the first time if that makes
> you more comfortable. `--files` mode never deletes anything.

## Command reference

```bash
# Email
uv run python accountant.py download --mailboxes gmail_personal
uv run python accountant.py download --all-mailboxes

# KSeF (defaults to the current month)
uv run python accountant.py ksef --role buyer
uv run python accountant.py ksef --role buyer --month 2026-02
uv run python accountant.py ksef --role buyer --month 2026-02 --bulk

# Rename
uv run python accountant.py rename --directory ./invoices
uv run python accountant.py rename --files invoice1.pdf invoice2.pdf

# Everything at once: download, rename, repair, report
uv run python accountant.py all --month 2026-06
uv run python accountant.py all --month 2026-06 --skip-download

# Repair invoices whose PDF failed to render (offline, no KSeF query)
uv run python accountant.py render

# Excel report (defaults to the previous month)
uv run python accountant.py excel
uv run python accountant.py excel --month 2026-06 --no-ai
uv run python accountant.py excel --month 2026-06 --output ~/raport.xlsx

# Any command: point at a different config, or turn up logging
uv run python accountant.py --config other.yaml --log-level DEBUG excel
```

## Development

```bash
uv run pytest              # tests
uv run black .             # format
uv run flake8 .            # lint
```

With mise, the same gate CI runs, in CI's order:

```bash
mise run check             # black --check, flake8, then pytest
```

CI runs on every pull request across Python 3.12, 3.13 and 3.14. The lint job
gates the test matrix, so formatting mistakes fail in seconds.

Adding a dependency goes through uv so the lockfile stays honest:

```bash
uv add some-package
uv add --dev some-dev-tool
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Unable to get page count` / `PDFInfoNotInstalledError` | poppler is missing (see Requirements). Affects `rename` only. |
| `Configuration error: Missing required config keys: ...` | `config.yaml` is missing a top-level section. Compare against `config.example.yaml`. |
| `No KSeF configuration found in config file` | Add a `ksef:` section with your `token`. |
| `No invoices found for YYYY-MM; no report written.` | Not an error. Either that month has no invoices, or `rename` has not moved them into `output/` yet. |
| `N invoice(s) have no KSeF number available from any source` | The KSeF number is not stored in the invoice XML — it comes from KSeF metadata. Invoices downloaded before the manifest existed only have it if their PDF rendered. Re-run `ksef` for that month to record it. |
| `N invoice(s) have no rendered PDF` | PDF rendering failed when those invoices were downloaded, so the report names their `.xml`. Fix it offline with `accountant.py render` — no re-download needed. |
| Excel column shows `#####` | Column too narrow in your viewer — widen it. The stored values are numbers. |
| `error parsing config file: mise.toml ... not trusted` | Run `mise trust` once in the repo. |

## Monthly workflow

One command does the whole routine — download, rename, repair missing PDFs, build the report:

```bash
uv run python accountant.py all --month 2026-06
```

Or run the steps individually:

```bash
uv run python accountant.py ksef   --role buyer --month 2026-06  # 1. download
uv run python accountant.py rename --directory ./invoices        # 2. rename and index
uv run python accountant.py render                               # 3. repair missing PDFs
uv run python accountant.py excel  --month 2026-06               # 4. build the .xlsx
```

`all` resolves the month once and passes it to every step, and stops at the first
failure. Add `--skip-download` to reuse invoices already on disk, or `--no-ai` to
skip expense classification.

Step 3 reads the FA(3) XML sidecars already on disk — it never queries KSeF, so it
is offline, repeatable, and can regenerate any past month. Add `--no-ai` to skip
description generation and spend no API calls.

## Features

- **📧 Email Search**: Downloads PDF invoices from multiple IMAP mailboxes using keywords (faktura, invoice, receipt)
- **🏛️ KSeF Integration**: Downloads invoices from Poland's national e-invoice system (Krajowy System e-Faktur), renders XML to PDF
- **🔍 NIP Detection**: Uses Claude AI to verify your NIP (Polish tax ID) in documents
- **🚫 Blacklist Filtering**: Skips PDFs containing configurable keywords (e.g. "polisa", "warunki") before sending to LLM
- **📁 Smart Organization**:
  - Your invoices → `./invoices/YYYY-MM/`
  - KSeF invoices → `./invoices/YYYY-MM/` (same structure, named with `ksef.pdf` suffix)
  - Client invoices → `./invoices/<client-nip>/`
  - Uncertain → `./invoices/uncertain/`
- **✨ Auto-rename**: Extracts date, company, invoice number from PDFs and writes renamed copies into `<directory>/output/` (the source directory is left with originals only)
- **📊 Excel report for the accountant**: `excel` builds one `.xlsx` per month with a row
  per invoice — NIP, counterparty, KSeF number, document number, date, net/VAT/gross as
  summable numbers, currency, an expense category, the local filename, and every line item
  flattened into one cell. Duplicate copies of the same invoice are collapsed so totals
  aren't double-counted. Generated workbooks are gitignored because they contain real
  counterparty data.
- **🏷️ Consistent expense categories**: the description column is drawn from a fixed list
  in `excel.categories`, so the same kind of expense reads the same way every month and
  the column can be sorted and filtered.
- **⛔ Non-deductible flagging**: expenses that can't be a cost of earning revenue
  (sports cards, meals, supplements, toys) are matched by keyword before any API call and
  labelled `NIE PODLEGA ODLICZENIU` in red. The rows stay in the report — nothing
  disappears silently — so you exclude them when totalling.
- **🔄 Deduplication** (runs during `rename`, destructive to the source directory):
  - **Byte-identical dupes**: source PDFs are grouped by SHA256 before renaming; for each group only one copy is kept, the rest are deleted. Source PDFs whose hash already matches a file in `output/` are also deleted.
  - **Logical dupes**: after Claude extracts invoice metadata, files matching an already-processed `(company, invoice_number)` pair are skipped and the source PDF is deleted.
  - `--files` mode (explicit file list) performs the same detection but never deletes source files.

## Configuration

Edit `config.yaml`:
```yaml
anthropic:
  api_key: "your-claude-api-key"
nip: "1234567890"  # Your 10-digit NIP
mailboxes:
  gmail_personal:
    host: "imap.gmail.com"
    username: "you@gmail.com"
    password: "app-password"  # Use Gmail App Password
```

## KSeF Setup

1. Log in at [podatki.gov.pl/ksef](https://www.podatki.gov.pl/ksef/)
2. Generate an authorization token for your NIP
3. Add the token to `config.yaml` under the `ksef` section

```bash
# Download purchase invoices for current month
uv run python accountant.py ksef --role buyer

# Download for a specific month
uv run python accountant.py ksef --role buyer --month 2026-02

# Download sales invoices
uv run python accountant.py ksef --role seller --month 2026-01
```

## Gmail Setup

1. Enable IMAP in Gmail settings
2. Create App Password: Google Account → Security → 2-Step Verification → App passwords
3. Use app password in config, NOT regular password

## QNAP Sync (optional)

If your accountant uses a QNAP NAS, install [QSync Client](https://gist.github.com/kamkarthi/7d71bf951d44c87321ca227e66796810) to sync invoices directly to their server.

On Fedora:
```bash
sudo ./scripts/qsync-install.sh          # install
sudo ./scripts/qsync-install.sh uninstall # uninstall
```

Then map the QNAP shared folder to a local directory on your system so your workflow can drop files there.

## Output

- `./invoices/YYYY-MM/` - Your invoices by month
- `./invoices/<nip>/` - Invoices you issued to clients
- `./invoices/uncertain/` - Needs manual review
- `<rename-directory>/output/` - Renamed copies produced by `rename --directory` (created automatically; source directory keeps originals, minus any duplicates removed by dedup)
- `./invoice_download.log` - Detailed logs
