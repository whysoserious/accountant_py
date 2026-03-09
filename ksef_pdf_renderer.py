#!/usr/bin/env python3
"""Render KSeF FA(3) XML invoices to PDF."""

from xml.etree import ElementTree
from typing import List, Optional
from dataclasses import dataclass, field
from fpdf import FPDF

NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"


def _t(el: Optional[ElementTree.Element], tag: str) -> str:
    """Get text from a child element, namespace-aware."""
    if el is None:
        return ""
    child = el.find(f"{{{NS}}}{tag}")
    if child is None:
        # Try without namespace as fallback
        child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _find(el: Optional[ElementTree.Element], tag: str) -> Optional[ElementTree.Element]:
    """Find a child element, namespace-aware."""
    if el is None:
        return None
    child = el.find(f"{{{NS}}}{tag}")
    if child is None:
        child = el.find(tag)
    return child


def _findall(el: Optional[ElementTree.Element], tag: str) -> list:
    """Find all child elements, namespace-aware."""
    if el is None:
        return []
    children = el.findall(f"{{{NS}}}{tag}")
    if not children:
        children = el.findall(tag)
    return children


@dataclass
class LineItem:
    """Invoice line item."""

    row_num: str
    description: str
    unit: str
    quantity: str
    unit_price: str
    net_amount: str
    vat_rate: str


@dataclass
class ParsedInvoice:
    """Parsed KSeF invoice data."""

    invoice_number: str = ""
    issue_date: str = ""
    sale_date: str = ""
    issue_place: str = ""
    currency: str = "PLN"
    invoice_type: str = ""

    seller_nip: str = ""
    seller_name: str = ""
    seller_address1: str = ""
    seller_address2: str = ""
    seller_email: str = ""
    seller_phone: str = ""

    buyer_nip: str = ""
    buyer_name: str = ""
    buyer_address1: str = ""
    buyer_address2: str = ""

    net_total_23: str = ""
    vat_total_23: str = ""
    net_total_8: str = ""
    vat_total_8: str = ""
    gross_total: str = ""

    line_items: List[LineItem] = field(default_factory=list)
    additional_info: List[tuple] = field(default_factory=list)

    payment_description: str = ""
    bank_account: str = ""
    bank_name: str = ""

    footer_text: str = ""


def parse_ksef_xml(xml_bytes: bytes) -> ParsedInvoice:
    """Parse KSeF FA(3) XML into structured data."""
    root = ElementTree.fromstring(xml_bytes)
    inv = ParsedInvoice()

    # Seller (Podmiot1)
    p1 = _find(root, "Podmiot1")
    p1_id = _find(p1, "DaneIdentyfikacyjne")
    inv.seller_nip = _t(p1_id, "NIP")
    inv.seller_name = _t(p1_id, "Nazwa")
    p1_addr = _find(p1, "Adres")
    inv.seller_address1 = _t(p1_addr, "AdresL1")
    inv.seller_address2 = _t(p1_addr, "AdresL2")
    for kontakt in _findall(p1, "DaneKontaktowe"):
        if _t(kontakt, "Email"):
            inv.seller_email = _t(kontakt, "Email")
        if _t(kontakt, "Telefon"):
            inv.seller_phone = _t(kontakt, "Telefon")

    # Buyer (Podmiot2)
    p2 = _find(root, "Podmiot2")
    p2_id = _find(p2, "DaneIdentyfikacyjne")
    inv.buyer_nip = _t(p2_id, "NIP")
    inv.buyer_name = _t(p2_id, "Nazwa")
    p2_addr = _find(p2, "Adres")
    inv.buyer_address1 = _t(p2_addr, "AdresL1")
    inv.buyer_address2 = _t(p2_addr, "AdresL2")

    # Invoice data (Fa)
    fa = _find(root, "Fa")
    inv.currency = _t(fa, "KodWaluty") or "PLN"
    inv.issue_date = _t(fa, "P_1")
    inv.issue_place = _t(fa, "P_1M")
    inv.invoice_number = _t(fa, "P_2")
    inv.sale_date = _t(fa, "P_6")
    inv.net_total_23 = _t(fa, "P_13_1")
    inv.vat_total_23 = _t(fa, "P_14_1")
    inv.net_total_8 = _t(fa, "P_13_2")
    inv.vat_total_8 = _t(fa, "P_14_2")
    inv.gross_total = _t(fa, "P_15")
    inv.invoice_type = _t(fa, "RodzajFaktury")

    # Line items
    for wiersz in _findall(fa, "FaWiersz"):
        item = LineItem(
            row_num=_t(wiersz, "NrWierszaFa"),
            description=_t(wiersz, "P_7"),
            unit=_t(wiersz, "P_8A"),
            quantity=_t(wiersz, "P_8B"),
            unit_price=_t(wiersz, "P_9A"),
            net_amount=_t(wiersz, "P_11"),
            vat_rate=_t(wiersz, "P_12"),
        )
        inv.line_items.append(item)

    # Additional descriptions
    for opis in _findall(fa, "DodatkowyOpis"):
        key = _t(opis, "Klucz")
        val = _t(opis, "Wartosc")
        if key:
            inv.additional_info.append((key, val))

    # Payment
    platnosc = _find(fa, "Platnosc")
    inv.payment_description = _t(platnosc, "OpisPlatnosci")
    rb = _find(platnosc, "RachunekBankowy")
    inv.bank_account = _t(rb, "NrRB")
    inv.bank_name = _t(rb, "NazwaBanku")

    # Footer
    stopka = _find(root, "Stopka")
    info = _find(stopka, "Informacje")
    inv.footer_text = _t(info, "StopkaFaktury")

    return inv


class InvoicePDF(FPDF):
    """PDF renderer for KSeF invoices."""

    FONT_NAME = "invoice_font"

    # Font search paths, in order of preference
    FONT_PATHS = [
        # Liberation Sans (Fedora)
        (
            "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
            "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
        ),
        # DejaVu Sans (Debian/Ubuntu)
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        # DejaVu Sans (older Fedora)
        (
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
        ),
    ]

    def __init__(self):
        super().__init__()
        self.add_page()
        self._load_font()
        self.set_auto_page_break(auto=True, margin=15)

    def _load_font(self) -> None:
        """Load a Unicode-capable TTF font from system fonts."""
        import os

        for regular, bold in self.FONT_PATHS:
            if os.path.exists(regular) and os.path.exists(bold):
                self.add_font(self.FONT_NAME, "", regular)
                self.add_font(self.FONT_NAME, "B", bold)
                return

        raise RuntimeError(
            "No suitable TTF font found. Install liberation-sans-fonts or dejavu-sans-fonts."
        )

    def _heading(self, text: str) -> None:
        self.set_font(self.FONT_NAME, "B", 11)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(1)

    def _label_value(self, label: str, value: str) -> None:
        self.set_font(self.FONT_NAME, "B", 8)
        self.cell(40, 5, label)
        self.set_font(self.FONT_NAME, "", 8)
        self.cell(0, 5, value, new_x="LMARGIN", new_y="NEXT")

    def _party_block(self, title: str, name: str, nip: str, addr1: str, addr2: str):
        self._heading(title)
        self._label_value("Nazwa:", name)
        self._label_value("NIP:", nip)
        self._label_value("Adres:", addr1)
        if addr2:
            self._label_value("", addr2)
        self.ln(2)


def render_invoice_pdf(xml_bytes: bytes) -> bytes:
    """
    Render KSeF XML invoice to PDF.

    Args:
        xml_bytes: Raw KSeF FA(3) XML content

    Returns:
        PDF file content as bytes
    """
    inv = parse_ksef_xml(xml_bytes)
    pdf = InvoicePDF()

    # Title
    pdf.set_font(pdf.FONT_NAME, "B", 14)
    title = f"Faktura {inv.invoice_type} {inv.invoice_number}"
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)

    # Dates
    pdf.set_font(pdf.FONT_NAME, "", 9)
    date_line = f"Data wystawienia: {inv.issue_date}"
    if inv.sale_date and inv.sale_date != inv.issue_date:
        date_line += f"    Data sprzedazy: {inv.sale_date}"
    if inv.issue_place:
        date_line += f"    Miejsce: {inv.issue_place}"
    pdf.cell(0, 5, date_line, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(3)

    # Seller
    pdf._party_block(
        "Sprzedawca",
        inv.seller_name,
        inv.seller_nip,
        inv.seller_address1,
        inv.seller_address2,
    )
    if inv.seller_email:
        pdf._label_value("Email:", inv.seller_email)
    if inv.seller_phone:
        pdf._label_value("Tel:", inv.seller_phone)
    pdf.ln(2)

    # Buyer
    pdf._party_block(
        "Nabywca",
        inv.buyer_name,
        inv.buyer_nip,
        inv.buyer_address1,
        inv.buyer_address2,
    )
    pdf.ln(2)

    # Line items table
    if inv.line_items:
        pdf._heading("Pozycje faktury")
        pdf.set_font(pdf.FONT_NAME, "B", 7)

        # Header
        col_widths = [8, 72, 12, 18, 22, 22, 16, 20]
        headers = [
            "Lp",
            "Opis",
            "Jm.",
            "Ilosc",
            "Cena netto",
            "Wartosc",
            "VAT%",
            "Kwota VAT",
        ]
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 6, h, border=1, align="C")
        pdf.ln()

        pdf.set_font(pdf.FONT_NAME, "", 7)
        for item in inv.line_items:
            vat_amount = ""
            try:
                net = float(item.net_amount) if item.net_amount else 0
                rate = float(item.vat_rate) if item.vat_rate else 0
                vat_amount = f"{net * rate / 100:.2f}"
            except ValueError:
                pass

            row = [
                item.row_num,
                item.description[:50],
                item.unit,
                item.quantity,
                item.unit_price,
                item.net_amount,
                f"{item.vat_rate}%",
                vat_amount,
            ]
            for i, val in enumerate(row):
                align = "L" if i == 1 else "R"
                pdf.cell(col_widths[i], 5, val, border=1, align=align)
            pdf.ln()

    pdf.ln(3)

    # Totals
    pdf._heading("Podsumowanie")
    if inv.net_total_23:
        pdf._label_value("Netto (23%):", f"{inv.net_total_23} {inv.currency}")
        pdf._label_value("VAT (23%):", f"{inv.vat_total_23} {inv.currency}")
    if inv.net_total_8:
        pdf._label_value("Netto (8%):", f"{inv.net_total_8} {inv.currency}")
        pdf._label_value("VAT (8%):", f"{inv.vat_total_8} {inv.currency}")
    pdf.set_font(pdf.FONT_NAME, "B", 9)
    pdf._label_value("BRUTTO:", f"{inv.gross_total} {inv.currency}")
    pdf.ln(2)

    # Payment
    if inv.payment_description or inv.bank_account:
        pdf._heading("Platnosc")
        if inv.payment_description:
            pdf._label_value("Forma:", inv.payment_description)
        if inv.bank_account:
            pdf._label_value("Nr konta:", inv.bank_account)
        if inv.bank_name:
            pdf._label_value("Bank:", inv.bank_name)
        pdf.ln(2)

    # Additional info
    if inv.additional_info:
        for key, val in inv.additional_info:
            pdf._label_value(f"{key}:", val)
        pdf.ln(2)

    # Footer
    pdf.ln(5)
    pdf.set_font(pdf.FONT_NAME, "", 6)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(
        0,
        4,
        "Dokument wygenerowany z KSeF (Krajowy System e-Faktur)",
        new_x="LMARGIN",
        new_y="NEXT",
        align="C",
    )
    if inv.footer_text:
        pdf.cell(0, 4, inv.footer_text, new_x="LMARGIN", new_y="NEXT", align="C")

    return pdf.output()
