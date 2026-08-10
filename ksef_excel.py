#!/usr/bin/env python3
"""
Build the accountant's monthly Excel report from locally saved KSeF invoices.

The report is assembled from the FA(3) XML sidecars that ``ksef`` wrote and
``rename`` moved, never from a live KSeF query. Reading from disk keeps the
command offline, idempotent, free of API rate limits, and able to regenerate any
past month.

Every XML sidecar shares its base name with its rendered PDF, so the local
filename reported in the sheet is derived from the XML path rather than matched
heuristically.
"""

import glob
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from constants import CLAUDE_SONNET_MODEL, DEFAULT_TEMPERATURE

# The accountant's ten columns keep their exact headers and order so their
# existing process does not shift. "Nazwa pliku" and "pozycje" are appended.
COLUMN_HEADERS = [
    "NIP",
    "Kontrahent",
    "Numer KSeF",
    "Numer dokumentu",
    "Data wystawienia",
    "Netto",
    "VAT",
    "Brutto",
    "Waluta",
    "Opis faktury - czego dotyczy w działalnosci",
    "Nazwa pliku",
    "pozycje",
]

DESCRIPTION_COLUMN = 10
POZYCJE_COLUMN = 12
MONEY_COLUMNS = (6, 7, 8)
MONEY_FORMAT = "#,##0.00"
MAX_TOKENS_DESCRIPTION = 200

DESCRIPTION_PROMPT = """Jesteś księgowym. Na podstawie danych faktury zakupowej napisz
krótkie uzasadnienie, czego dotyczy wydatek w działalności gospodarczej — tak, aby
posłużyło jako podstawa odliczenia podatkowego.

Kontrahent: {counterparty}
Kwoty: netto {net}, VAT {vat}, brutto {gross} {currency}
Pozycje na fakturze:
{positions}

Odpowiedz jednym zdaniem po polsku, bez wstępu i bez cudzysłowów. Maksymalnie 200 znaków."""


@dataclass
class InvoicePosition:
    """One line item from an FA(3) ``FaWiersz`` element."""

    name: str
    unit: str
    quantity: Decimal
    unit_net: Decimal
    net: Decimal
    vat_rate: str

    @property
    def vat(self) -> Decimal:
        """VAT for this line, or zero when the rate is a code such as 'zw'."""
        if not _is_numeric_rate(self.vat_rate):
            return Decimal("0.00")
        value = self.net * Decimal(self.vat_rate) / Decimal(100)
        return value.quantize(Decimal("0.01"))

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


@dataclass
class InvoiceRecord:
    """One row of the report."""

    nip: str = ""
    counterparty: str = ""
    ksef_number: str = ""
    document_number: str = ""
    issue_date: str = ""
    net: Decimal = Decimal("0.00")
    vat: Decimal = Decimal("0.00")
    gross: Decimal = Decimal("0.00")
    currency: str = ""
    description: str = ""
    filename: str = ""
    positions: List[InvoicePosition] = field(default_factory=list)


def _local_name(tag: str) -> str:
    """Strip any namespace from an element tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first(element: ET.Element, name: str) -> Optional[ET.Element]:
    """Find the first descendant (or self) whose local name matches."""
    if _local_name(element.tag) == name:
        return element
    for child in element.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _children_matching(element: Optional[ET.Element], pattern: str) -> List[ET.Element]:
    """All descendants whose local name matches a regular expression."""
    if element is None:
        return []
    regex = re.compile(pattern)
    return [child for child in element.iter() if regex.fullmatch(_local_name(child.tag))]


def _text(element: Optional[ET.Element], name: str) -> str:
    """Text of the first matching descendant, stripped, or empty string."""
    if element is None:
        return ""
    found = _first(element, name)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _is_numeric_rate(rate: str) -> bool:
    try:
        Decimal(rate)
        return True
    except (InvalidOperation, ValueError, ArithmeticError):
        return False


def _to_decimal(text: str, default: str = "0.00") -> Decimal:
    """
    Parse an FA(3) monetary value.

    KSeF uses a dot decimal separator, but this tolerates a comma and embedded
    spaces so an oddly serialised document does not lose its amounts.
    """
    if not text:
        return Decimal(default)
    cleaned = text.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, ArithmeticError):
        return Decimal(default)


def _party(root: ET.Element, element_name: str) -> tuple:
    """Return (name, nip) for a Podmiot1/Podmiot2 element."""
    party = _first(root, element_name)
    if party is None:
        return "", ""
    return _text(party, "Nazwa"), _text(party, "NIP")


def parse_invoice_xml(xml_bytes: bytes, role: str = "buyer") -> InvoiceRecord:
    """
    Parse one FA(3) document into a report row.

    Args:
        xml_bytes: Raw FA(3) XML.
        role: ``"buyer"`` reports the seller as counterparty (purchase
            invoices); ``"seller"`` reports the buyer.

    Raises:
        ValueError: If the document is not parseable XML.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"Not a parseable XML document: {e}") from e

    fa = _first(root, "Fa")

    counterparty_element = "Podmiot1" if role == "buyer" else "Podmiot2"
    counterparty, nip = _party(root, counterparty_element)

    positions = [
        InvoicePosition(
            name=_text(row, "P_7"),
            unit=_text(row, "P_8A"),
            quantity=_to_decimal(_text(row, "P_8B")),
            unit_net=_to_decimal(_text(row, "P_9A")),
            net=_to_decimal(_text(row, "P_11")),
            vat_rate=_text(row, "P_12"),
        )
        for row in _children_matching(fa, r"FaWiersz")
    ]

    # Net and VAT totals are reported per VAT rate in numbered field pairs, so
    # every P_13_x / P_14_x must be summed -- not just the first.
    net_elements = _children_matching(fa, r"P_13_\d+")
    vat_elements = _children_matching(fa, r"P_14_\d+")

    if net_elements:
        net = sum((_to_decimal(e.text or "") for e in net_elements), Decimal("0.00"))
    else:
        # No per-rate net totals: recover it from the line items, which carry
        # their own net values. Leaving net at zero would make the VAT fallback
        # below attribute the entire gross amount to tax.
        net = sum((p.net for p in positions), Decimal("0.00"))

    vat = sum((_to_decimal(e.text or "") for e in vat_elements), Decimal("0.00"))

    gross_text = _text(fa, "P_15")
    gross = _to_decimal(gross_text) if gross_text else net + vat

    # Some documents omit the per-rate pairs entirely; recover VAT from the
    # difference rather than reporting zero.
    if not vat_elements:
        vat = gross - net

    return InvoiceRecord(
        nip=nip,
        counterparty=counterparty,
        ksef_number=_text(root, "NumerKSeFDokumentu"),
        document_number=_text(fa, "P_2"),
        issue_date=_text(fa, "P_1"),
        net=net,
        vat=vat,
        gross=gross,
        currency=_text(fa, "KodWaluty"),
        description="",
        filename="",
        positions=positions,
    )


def _format_quantity(quantity: Decimal) -> str:
    """Render a quantity without trailing zeros: 1.000 becomes 1."""
    normalized = quantity.normalize()
    if normalized == normalized.to_integral_value():
        try:
            return str(normalized.quantize(Decimal("1")))
        except InvalidOperation:
            return str(normalized)
    return str(normalized)


def _format_money(value: Decimal) -> str:
    return f"{value:.2f}"


def format_positions(positions: List[InvoicePosition]) -> str:
    """
    Flatten line items into a single cell, one numbered entry per line.

    Twelve columns wide, a single run-on line would be unreadable, so entries
    are newline-separated and the cell is set to wrap.
    """
    lines = []
    for number, position in enumerate(positions, start=1):
        rate = f"{position.vat_rate}%" if _is_numeric_rate(position.vat_rate) else position.vat_rate
        lines.append(
            f"{number}. {position.name} — "
            f"{_format_quantity(position.quantity)} {position.unit} "
            f"× {_format_money(position.unit_net)} = "
            f"netto {_format_money(position.net)}, "
            f"VAT {rate}, "
            f"brutto {_format_money(position.gross)}"
        )
    return "\n".join(lines)


def format_date(value: str) -> str:
    """Convert an ISO date to the DD.MM.YYYY order the accountant's sheet uses."""
    if not value:
        return ""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return value
    year, month, day = match.groups()
    return f"{day}.{month}.{year}"


def load_records(
    directory: str,
    role: str = "buyer",
    logger: Optional[logging.Logger] = None,
) -> List[InvoiceRecord]:
    """
    Load every FA(3) sidecar under ``directory``, recursively.

    Searching recursively rather than assuming a fixed layout means the command
    works whether invoices sit in ``invoices/output/`` or a month subdirectory.

    A document that fails to parse is skipped with a warning: one bad invoice
    must not cost the accountant the other twenty rows.
    """
    log = logger or logging.getLogger(__name__)

    if not os.path.isdir(directory):
        log.warning(f"Directory not found, nothing to report: {directory}")
        return []

    records: List[InvoiceRecord] = []
    for xml_path in sorted(glob.glob(os.path.join(directory, "**", "*.xml"), recursive=True)):
        try:
            with open(xml_path, "rb") as handle:
                record = parse_invoice_xml(handle.read(), role=role)
        except (ValueError, OSError) as e:
            log.warning(f"Skipping '{os.path.basename(xml_path)}': {e}")
            continue

        pdf_path = os.path.splitext(xml_path)[0] + ".pdf"
        record.filename = os.path.basename(pdf_path)
        if not os.path.exists(pdf_path):
            # PDF and XML collisions are resolved independently upstream, so the
            # pair can drift. Report the expected name rather than drop the row.
            log.warning(f"No PDF beside '{os.path.basename(xml_path)}'; reporting expected name.")

        records.append(record)

    records.sort(key=lambda r: (r.issue_date, r.document_number))
    log.info(f"Loaded {len(records)} invoice(s) from {directory}")
    return records


def filter_by_month(records: List[InvoiceRecord], month: Optional[str]) -> List[InvoiceRecord]:
    """
    Keep only records issued in ``month`` (``YYYY-MM``). ``None`` keeps all.

    Filtering on the issue date in the document, rather than on directory
    layout, keeps the result correct however the files happen to be arranged.
    """
    if not month:
        return list(records)
    return [r for r in records if r.issue_date.startswith(month)]


def build_workbook(records: List[InvoiceRecord]):
    """Assemble the report workbook. Requires no API access."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Faktury"

    sheet.append(COLUMN_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top")

    for record in records:
        sheet.append(
            [
                record.nip,
                record.counterparty,
                record.ksef_number,
                record.document_number,
                format_date(record.issue_date),
                float(record.net),
                float(record.vat),
                float(record.gross),
                record.currency,
                record.description,
                record.filename,
                format_positions(record.positions),
            ]
        )
        row = sheet.max_row
        for column in MONEY_COLUMNS:
            sheet.cell(row, column).number_format = MONEY_FORMAT
        sheet.cell(row, POZYCJE_COLUMN).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.cell(row, DESCRIPTION_COLUMN).alignment = Alignment(wrap_text=True, vertical="top")

    widths = [14, 34, 40, 20, 16, 12, 12, 12, 8, 46, 44, 60]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = width

    sheet.freeze_panes = "A2"
    return workbook


def write_report(records: List[InvoiceRecord], path: str) -> None:
    """Write the workbook to ``path``, creating parent directories as needed."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    build_workbook(records).save(path)


def describe_invoices(
    records: List[InvoiceRecord],
    client,
    logger: logging.Logger,
    model: str = CLAUDE_SONNET_MODEL,
) -> None:
    """
    Fill each record's ``description`` with a tax-deduction rationale.

    Sends only the counterparty, line items, and totals -- deliberately not the
    full XML, which carries more than the task requires.

    Failures are per-row: a failed call leaves that description empty, logs a
    warning, and never aborts the remaining invoices.
    """
    for record in records:
        prompt = DESCRIPTION_PROMPT.format(
            counterparty=record.counterparty or "nieznany",
            net=_format_money(record.net),
            vat=_format_money(record.vat),
            gross=_format_money(record.gross),
            currency=record.currency or "PLN",
            positions=format_positions(record.positions) or "(brak pozycji)",
        )
        try:
            message = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS_DESCRIPTION,
                temperature=DEFAULT_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
            record.description = message.content[0].text.strip()
        except Exception as e:
            # Deliberately broad: an SDK, network, or content error on one
            # invoice must not cost the rest of the report.
            logger.warning(f"Description failed for '{record.document_number}': {e}")
            record.description = ""
