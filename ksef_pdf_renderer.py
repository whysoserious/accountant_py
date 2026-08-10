#!/usr/bin/env python3
"""Render KSeF FA(3) XML invoices to PDF with QR code verification."""

import base64
import hashlib
import io
import os
from dataclasses import dataclass, field
from typing import List, Optional
from xml.etree import ElementTree

import qrcode
from fpdf import FPDF

NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"

# KSeF QR code verification base URLs by environment
KSEF_QR_BASE_URLS = {
    "production": "https://qr.ksef.mf.gov.pl",
    "test": "https://qr-test.ksef.mf.gov.pl",
    "demo": "https://qr-demo.ksef.mf.gov.pl",
}


def _t(el: Optional[ElementTree.Element], tag: str) -> str:
    """Get text from a child element, namespace-aware."""
    if el is None:
        return ""
    child = el.find(f"{{{NS}}}{tag}")
    if child is None:
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
    ksef_number: str = ""

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
    net_total_5: str = ""
    vat_total_5: str = ""
    net_total_0: str = ""
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
    inv.net_total_5 = _t(fa, "P_13_3")
    inv.vat_total_5 = _t(fa, "P_14_3")
    inv.net_total_0 = _t(fa, "P_13_6_1")
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


def _generate_ksef_qr(
    xml_bytes: bytes,
    seller_nip: str,
    issue_date: str,
    environment: str = "production",
) -> tuple:
    """
    Generate KSeF CODE I verification QR code.

    Args:
        xml_bytes: Raw invoice XML bytes
        seller_nip: Seller's NIP (10-digit tax ID)
        issue_date: Invoice issue date in YYYY-MM-DD format
        environment: KSeF environment name

    Returns:
        Tuple of (qr_png_bytes, verification_url)
    """
    nip = seller_nip.replace("-", "").replace(" ", "")

    # Convert YYYY-MM-DD to DD-MM-YYYY
    parts = issue_date.split("-")
    date_str = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else issue_date

    # SHA-256 hash of XML bytes, base64url-encoded without padding
    xml_hash = hashlib.sha256(xml_bytes).digest()
    hash_b64url = base64.urlsafe_b64encode(xml_hash).rstrip(b"=").decode("ascii")

    base_url = KSEF_QR_BASE_URLS.get(environment, KSEF_QR_BASE_URLS["production"])
    verification_url = f"{base_url}/invoice/{nip}/{date_str}/{hash_b64url}"

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(verification_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), verification_url


class InvoicePDF(FPDF):
    """PDF renderer for KSeF invoices."""

    FONT_NAME = "invoice_font"

    HEADER_BG = (245, 245, 245)
    SEPARATOR_COLOR = (200, 200, 200)
    GRAY_TEXT = (128, 128, 128)
    BLACK_TEXT = (0, 0, 0)

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
        for regular, bold in self.FONT_PATHS:
            if os.path.exists(regular) and os.path.exists(bold):
                self.add_font(self.FONT_NAME, "", regular)
                self.add_font(self.FONT_NAME, "B", bold)
                return

        raise RuntimeError(
            "No suitable TTF font found. " "Install liberation-sans-fonts or dejavu-sans-fonts."
        )

    def _separator(self) -> None:
        """Draw a thin horizontal separator line."""
        self.set_draw_color(*self.SEPARATOR_COLOR)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_draw_color(0, 0, 0)

    def _heading(self, text: str) -> None:
        """Draw a section heading with gray background."""
        self.set_font(self.FONT_NAME, "B", 11)
        self.set_fill_color(*self.HEADER_BG)
        self.cell(0, 7, f"  {text}", new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(1)

    def _label_value(self, label: str, value: str) -> None:
        """Draw a label: value pair."""
        self.set_font(self.FONT_NAME, "B", 8)
        self.cell(40, 5, label)
        self.set_font(self.FONT_NAME, "", 8)
        self.cell(0, 5, value, new_x="LMARGIN", new_y="NEXT")

    def _party_block(self, title: str, name: str, nip: str, addr1: str, addr2: str) -> None:
        """Draw a party information block (seller/buyer)."""
        self._heading(title)
        self._label_value("Nazwa:", name)
        self._label_value("NIP:", nip)
        self._label_value("Adres:", addr1)
        if addr2:
            self._label_value("", addr2)
        self.ln(2)

    def _table_header_row(self, headers: List[str], col_widths: List[int]) -> None:
        """Draw a table header row with gray background."""
        self.set_font(self.FONT_NAME, "B", 7)
        self.set_fill_color(*self.HEADER_BG)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 6, h, border=1, align="C", fill=True)
        self.ln()


def render_invoice_pdf(
    xml_bytes: bytes,
    ksef_number: Optional[str] = None,
    seller_nip: Optional[str] = None,
    environment: str = "production",
) -> bytes:
    """
    Render KSeF XML invoice to PDF.

    Args:
        xml_bytes: Raw KSeF FA(3) XML content
        ksef_number: KSeF reference number (for display and QR code)
        seller_nip: Seller's NIP for QR code generation (fallback: parsed from XML)
        environment: KSeF environment for QR verification URL

    Returns:
        PDF file content as bytes
    """
    inv = parse_ksef_xml(xml_bytes)
    if ksef_number:
        inv.ksef_number = ksef_number
    if not seller_nip:
        seller_nip = inv.seller_nip

    pdf = InvoicePDF()

    # --- Title block ---
    pdf.set_font(pdf.FONT_NAME, "B", 14)
    title = f"Faktura {inv.invoice_type} {inv.invoice_number}"
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C")

    if inv.ksef_number:
        pdf.set_font(pdf.FONT_NAME, "", 8)
        pdf.set_text_color(*pdf.GRAY_TEXT)
        pdf.cell(
            0,
            5,
            f"KSeF: {inv.ksef_number}",
            new_x="LMARGIN",
            new_y="NEXT",
            align="C",
        )
        pdf.set_text_color(*pdf.BLACK_TEXT)

    pdf.ln(2)

    # --- Dates ---
    pdf.set_font(pdf.FONT_NAME, "", 9)
    date_line = f"Data wystawienia: {inv.issue_date}"
    if inv.sale_date and inv.sale_date != inv.issue_date:
        date_line += f"    Data sprzedazy: {inv.sale_date}"
    if inv.issue_place:
        date_line += f"    Miejsce: {inv.issue_place}"
    pdf.cell(0, 5, date_line, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)
    pdf._separator()

    # --- Seller ---
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
    pdf.ln(1)

    # --- Buyer ---
    pdf._party_block(
        "Nabywca",
        inv.buyer_name,
        inv.buyer_nip,
        inv.buyer_address1,
        inv.buyer_address2,
    )
    pdf._separator()

    # --- Line items table ---
    if inv.line_items:
        pdf._heading("Pozycje faktury")

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
        pdf._table_header_row(headers, col_widths)

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

    # --- Tax rate summary table ---
    pdf._heading("Podsumowanie")

    tax_col_widths = [30, 40, 40, 40, 40]
    tax_headers = ["Stawka", "Netto", "VAT", "Brutto", "Waluta"]
    pdf._table_header_row(tax_headers, tax_col_widths)

    pdf.set_font(pdf.FONT_NAME, "", 7)
    tax_rows = []
    if inv.net_total_23:
        try:
            net = float(inv.net_total_23)
            vat = float(inv.vat_total_23) if inv.vat_total_23 else 0
            tax_rows.append(("23%", net, vat, net + vat))
        except ValueError:
            pass
    if inv.net_total_8:
        try:
            net = float(inv.net_total_8)
            vat = float(inv.vat_total_8) if inv.vat_total_8 else 0
            tax_rows.append(("8%", net, vat, net + vat))
        except ValueError:
            pass
    if inv.net_total_5:
        try:
            net = float(inv.net_total_5)
            vat = float(inv.vat_total_5) if inv.vat_total_5 else 0
            tax_rows.append(("5%", net, vat, net + vat))
        except ValueError:
            pass
    if inv.net_total_0:
        try:
            net = float(inv.net_total_0)
            tax_rows.append(("0%", net, 0.0, net))
        except ValueError:
            pass

    for rate_label, net, vat, gross in tax_rows:
        row_vals = [
            rate_label,
            f"{net:.2f}",
            f"{vat:.2f}",
            f"{gross:.2f}",
            inv.currency,
        ]
        for i, val in enumerate(row_vals):
            align = "R" if i in (1, 2, 3) else "C"
            pdf.cell(tax_col_widths[i], 5, val, border=1, align=align)
        pdf.ln()

    # Gross total row
    pdf.set_font(pdf.FONT_NAME, "B", 8)
    pdf.cell(tax_col_widths[0], 6, "RAZEM", border=1, align="C")
    total_net = sum(r[1] for r in tax_rows)
    total_vat = sum(r[2] for r in tax_rows)
    pdf.cell(tax_col_widths[1], 6, f"{total_net:.2f}", border=1, align="R")
    pdf.cell(tax_col_widths[2], 6, f"{total_vat:.2f}", border=1, align="R")
    pdf.cell(
        tax_col_widths[3],
        6,
        inv.gross_total if inv.gross_total else f"{total_net + total_vat:.2f}",
        border=1,
        align="R",
    )
    pdf.cell(tax_col_widths[4], 6, inv.currency, border=1, align="C")
    pdf.ln()
    pdf.ln(2)

    # --- Payment ---
    if inv.payment_description or inv.bank_account:
        pdf._heading("Platnosc")
        if inv.payment_description:
            pdf._label_value("Forma:", inv.payment_description)
        if inv.bank_account:
            pdf._label_value("Nr konta:", inv.bank_account)
        if inv.bank_name:
            pdf._label_value("Bank:", inv.bank_name)
        pdf.ln(2)

    # --- Additional info ---
    if inv.additional_info:
        pdf._separator()
        for key, val in inv.additional_info:
            pdf._label_value(f"{key}:", val)
        pdf.ln(2)

    # --- QR Code ---
    if seller_nip and inv.issue_date:
        try:
            qr_bytes, verification_url = _generate_ksef_qr(
                xml_bytes, seller_nip, inv.issue_date, environment
            )
            pdf._separator()
            pdf.set_font(pdf.FONT_NAME, "B", 9)
            pdf.cell(
                0,
                6,
                "Weryfikacja KSeF",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(1)

            qr_x = pdf.get_x()
            qr_y = pdf.get_y()
            pdf.image(io.BytesIO(qr_bytes), x=qr_x, y=qr_y, w=40, h=40)

            pdf.set_xy(qr_x + 45, qr_y + 2)
            pdf.set_font(pdf.FONT_NAME, "", 7)
            pdf.set_text_color(*pdf.GRAY_TEXT)

            if inv.ksef_number:
                pdf.cell(0, 4, f"Nr KSeF: {inv.ksef_number}", new_x="LEFT", new_y="NEXT")
                pdf.set_x(qr_x + 45)

            pdf.cell(0, 4, "Link weryfikacyjny:", new_x="LEFT", new_y="NEXT")
            pdf.set_x(qr_x + 45)

            # Split long URL across lines if needed
            url_line_max = 80
            for i in range(0, len(verification_url), url_line_max):
                pdf.cell(
                    0,
                    4,
                    verification_url[i : i + url_line_max],
                    new_x="LEFT",
                    new_y="NEXT",
                )
                pdf.set_x(qr_x + 45)

            pdf.set_text_color(*pdf.BLACK_TEXT)
            pdf.set_y(max(pdf.get_y(), qr_y + 42))
            pdf.ln(3)
        except Exception:
            pass

    # --- Footer ---
    pdf.ln(3)
    pdf.set_font(pdf.FONT_NAME, "", 6)
    pdf.set_text_color(*pdf.GRAY_TEXT)
    footer_text = "Dokument wygenerowany z KSeF (Krajowy System e-Faktur)"
    if inv.ksef_number:
        footer_text += f" | {inv.ksef_number}"
    pdf.cell(0, 4, footer_text, new_x="LMARGIN", new_y="NEXT", align="C")
    if inv.footer_text:
        pdf.cell(0, 4, inv.footer_text, new_x="LMARGIN", new_y="NEXT", align="C")

    return pdf.output()
