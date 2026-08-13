#!/usr/bin/env python3
"""Configuration parser for accountant script."""

import yaml
import os
from typing import Dict, List, Optional
from dataclasses import dataclass

# Imported for its default categories, keywords and prompt. ksef_excel does not
# import this module, so there is no cycle.
import ksef_excel


@dataclass
class MailboxConfig:
    """Configuration for a single mailbox."""

    name: str
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool = True


@dataclass
class SearchConfig:
    """Configuration for email search."""

    days_back: int
    keywords: List[str]


@dataclass
class OutputConfig:
    """Configuration for output directories and logging."""

    main_directory: str
    uncertain_directory: str
    log_file: str


@dataclass
class PromptsConfig:
    """Configuration for AI prompts."""

    nip_check: str
    categorization: str


@dataclass
class FilterConfig:
    """Configuration for attachment filtering."""

    blacklist_keywords: List[str]


@dataclass
class KSeFConfig:
    """Configuration for KSeF integration."""

    token: str
    environment: str = "production"


@dataclass
class ExcelConfig:
    """
    Configuration for the accountant's Excel report.

    ``categories`` constrains what the model may write in the description
    column, so the same kind of expense gets the same label every month and the
    accountant can sort and filter on it.

    ``non_deductible_keywords`` marks expenses that cannot be deducted at all.
    Matching rows are kept in the report and labelled rather than removed, so
    nothing silently vanishes from the accountant's file.
    """

    categories: List[str]
    non_deductible_keywords: List[str]
    non_deductible_label: str
    description_prompt: str


@dataclass
class Config:
    """Main configuration class."""

    api_key: str
    nip: str
    search: SearchConfig
    mailboxes: Dict[str, MailboxConfig]
    output: OutputConfig
    prompts: PromptsConfig
    filter: FilterConfig
    excel: "ExcelConfig"
    ksef: Optional[KSeFConfig] = None


def load_config(config_path: str) -> Config:
    """
    Load and parse configuration from YAML file.

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        Config object with parsed configuration

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid or missing required fields
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config file: {e}")

    # Validate required top-level keys
    required_keys = ["anthropic", "nip", "search", "mailboxes", "output"]
    missing_keys = [key for key in required_keys if key not in config_data]
    if missing_keys:
        raise ValueError(f"Missing required config keys: {', '.join(missing_keys)}")

    # Parse Anthropic config
    if "api_key" not in config_data["anthropic"]:
        raise ValueError("Missing 'api_key' in 'anthropic' section")
    api_key = config_data["anthropic"]["api_key"]

    # Parse NIP
    nip = str(config_data["nip"]).strip()
    if not nip:
        raise ValueError("NIP cannot be empty")

    # Parse search config
    search_data = config_data["search"]
    search = SearchConfig(
        days_back=search_data.get("days_back", 45),
        keywords=search_data.get("keywords", ["invoice", "faktura", "receipt"]),
    )

    # Parse mailboxes
    mailboxes_data = config_data["mailboxes"]
    if not mailboxes_data:
        raise ValueError("At least one mailbox must be configured")

    mailboxes = {}
    for name, mb_config in mailboxes_data.items():
        required_mb_keys = ["host", "port", "username", "password"]
        missing_mb_keys = [key for key in required_mb_keys if key not in mb_config]
        if missing_mb_keys:
            raise ValueError(f"Missing keys in mailbox '{name}': {', '.join(missing_mb_keys)}")

        mailboxes[name] = MailboxConfig(
            name=name,
            host=mb_config["host"],
            port=int(mb_config["port"]),
            username=mb_config["username"],
            password=mb_config["password"],
            use_ssl=mb_config.get("use_ssl", True),
        )

    # Parse output config
    output_data = config_data["output"]
    output = OutputConfig(
        main_directory=output_data.get("main_directory", "./invoices"),
        uncertain_directory=output_data.get("uncertain_directory", "./invoices/uncertain"),
        log_file=output_data.get("log_file", "./invoice_download.log"),
    )

    # Parse prompts config (with defaults)
    prompts_data = config_data.get("prompts", {})

    default_nip_check = """Analyze this document (invoice, receipt, or proof of purchase).
Check if it contains the following NIP (Polish tax ID): {nip}

The NIP might be formatted as:
- Plain number: 1234567890
- With dashes: 123-456-78-90
- With spaces: 123 456 78 90
- Labeled as "NIP:", "NIP nabywcy:", "Tax ID:", etc.

Respond with ONLY "YES" if the document contains this specific NIP, or "NO" if it doesn't.
If you cannot read the document clearly, respond with "NO"."""

    default_categorization = """Extract the following information from this invoice:

Please extract:
1. Invoice date (in format YYYY-MM-DD)
2. Company name
3. Invoice number
4. Brief description of what the invoice is for

For the description, be concise and use categories like these examples:
- abonament na oprogramowanie
- akcesoria do druku 3d
- domena internetowa
- doradztwo IT
- filtry do wody
- herbata do biura
- karta pamięci
- licencja na oprogramowanie
- materiały do druku
- narzędzia
- olej silnikowy
- paliwo
- remont biura
- serwis samochodu
- słuchawki
- środki czystości
- usługi księgowe
- usługi telekomunikacyjne
- zegarek elektroniczny

Respond in this exact JSON format:
{
  "date": "YYYY-MM-DD",
  "company": "Company Name",
  "invoice_number": "INV12345",
  "description": "Brief description"
}

If you can't find some information, use "Unknown" as the value."""

    prompts = PromptsConfig(
        nip_check=prompts_data.get("nip_check", default_nip_check),
        categorization=prompts_data.get("categorization", default_categorization),
    )

    # Parse filter config (optional)
    filter_data = config_data.get("filter", {})
    filter_config = FilterConfig(
        blacklist_keywords=filter_data.get("blacklist_keywords", []),
    )

    # Parse Excel report config (optional). Defaults live in ksef_excel so the
    # module that uses them owns them, rather than being duplicated here.
    excel_data = config_data.get("excel", {}) or {}
    excel = ExcelConfig(
        categories=excel_data.get("categories") or list(ksef_excel.DEFAULT_CATEGORIES),
        non_deductible_keywords=(
            excel_data.get("non_deductible_keywords")
            if excel_data.get("non_deductible_keywords") is not None
            else list(ksef_excel.DEFAULT_NON_DEDUCTIBLE_KEYWORDS)
        ),
        non_deductible_label=excel_data.get(
            "non_deductible_label", ksef_excel.DEFAULT_NON_DEDUCTIBLE_LABEL
        ),
        description_prompt=excel_data.get("description_prompt", ksef_excel.DESCRIPTION_PROMPT),
    )

    # Parse KSeF config (optional)
    ksef = None
    ksef_data = config_data.get("ksef")
    if ksef_data:
        if "token" not in ksef_data:
            raise ValueError("Missing 'token' in 'ksef' section")
        ksef = KSeFConfig(
            token=ksef_data["token"],
            environment=ksef_data.get("environment", "production"),
        )

    return Config(
        api_key=api_key,
        nip=nip,
        search=search,
        mailboxes=mailboxes,
        output=output,
        prompts=prompts,
        filter=filter_config,
        excel=excel,
        ksef=ksef,
    )


def validate_mailbox_names(config: Config, requested_mailboxes: Optional[List[str]]) -> List[str]:
    """
    Validate that requested mailboxes exist in config.

    Args:
        config: Configuration object
        requested_mailboxes: List of mailbox names to use, or None for all

    Returns:
        List of validated mailbox names

    Raises:
        ValueError: If any requested mailbox doesn't exist in config
    """
    if requested_mailboxes is None:
        # Return all configured mailboxes
        return list(config.mailboxes.keys())

    # Validate that all requested mailboxes exist
    available = set(config.mailboxes.keys())
    requested = set(requested_mailboxes)
    missing = requested - available

    if missing:
        raise ValueError(
            f"Unknown mailboxes: {', '.join(missing)}. " f"Available: {', '.join(available)}"
        )

    return requested_mailboxes
