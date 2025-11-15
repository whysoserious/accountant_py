# Accountant - Polish Invoice Manager

## Quick Start

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip3 install -r requirements.txt
cp config.example.yaml config.yaml  # Edit with your details

# Download invoices from email (last 45 days)
python3 accountant.py --config config.yaml download --mailboxes gmail_personal

# Download from all configured mailboxes
python3 accountant.py --config config.yaml download --all-mailboxes

# Rename PDFs based on content
python3 accountant.py --config config.yaml rename --directory ./invoices
```

## Features

- **📧 Email Search**: Downloads PDF invoices from multiple IMAP mailboxes using keywords (faktura, invoice, receipt)
- **🔍 NIP Detection**: Uses Claude AI to verify your NIP (Polish tax ID) in documents
- **📁 Smart Organization**:
  - Your invoices → `./invoices/YYYY-MM/`
  - Client invoices → `./invoices/<client-nip>/`
  - Uncertain → `./invoices/uncertain/`
- **✨ Auto-rename**: Extracts date, company, invoice number from PDFs and renames files

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

## Gmail Setup

1. Enable IMAP in Gmail settings
2. Create App Password: Google Account → Security → 2-Step Verification → App passwords
3. Use app password in config, NOT regular password

## Output

- `./invoices/YYYY-MM/` - Your invoices by month
- `./invoices/<nip>/` - Invoices you issued to clients
- `./invoices/uncertain/` - Needs manual review
- `./invoice_download.log` - Detailed logs