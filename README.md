# Accountant Python

Simple utility for renaming invoice files.

## Setup

Complete sequence of commands to set up the environment and run the script:

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip3 install -r requirements.txt

# Run the script
python3 rename_invoices.py --files <filename> --api-key <your_api_key>
```

Replace `<filename>` with your invoice file and `<your_api_key>` with your Claude API key.

## Usage

If you've already set up the environment, you can run the script with:

```bash
source venv/bin/activate  # If not already activated
python3 rename_invoices.py --files <filename> --api-key <your_api_key>
```