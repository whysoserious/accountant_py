# Accountant - Polish Invoice Manager

## Quick Start

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip3 install -r requirements.txt
cp config.example.yaml config.yaml  # Edit with your details

# Download invoices from email (last 45 days)
python3 accountant.py download --mailboxes gmail_personal

# Download from all configured mailboxes
python3 accountant.py download --all-mailboxes

# Download invoices from KSeF (current month)
python3 accountant.py ksef --role buyer

# Download invoices from KSeF for a specific month
python3 accountant.py ksef --role buyer --month 2026-02

# Rename PDFs based on content (writes to ./invoices/output/)
python3 accountant.py rename --directory ./invoices
```

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
python3 accountant.py ksef --role buyer

# Download for a specific month
python3 accountant.py ksef --role buyer --month 2026-02

# Download sales invoices
python3 accountant.py ksef --role seller --month 2026-01
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
