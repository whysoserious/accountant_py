#!/usr/bin/env python3
"""
Synthetic test data builders.

No real invoice data may enter this repository. Everything here is fabricated:
company names are invented, NIPs are generated to satisfy the Polish checksum
without belonging to any real taxpayer, and the FA(3) documents are hand-built
minimal-but-valid structures rather than captured production payloads.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

# Weights used by the Polish NIP checksum, applied to the first nine digits.
NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def nip_check_digit(first_nine: str) -> int:
    """Return the checksum digit for the first nine digits of a NIP."""
    return sum(int(d) * w for d, w in zip(first_nine, NIP_WEIGHTS)) % 11


def synthetic_nip(seed: str = "100000000") -> str:
    """
    Build a checksum-valid but entirely fabricated NIP.

    Starts at ``seed`` and walks forward until the checksum is a single digit
    (a remainder of 10 cannot appear in a valid NIP). Deliberately begins in a
    numeric range that no real Polish taxpayer identifier occupies.
    """
    candidate = int(seed)
    while True:
        base = f"{candidate:09d}"
        check = nip_check_digit(base)
        if check != 10:
            return base + str(check)
        candidate += 1


SELLER_NIP = synthetic_nip("100000000")
BUYER_NIP = synthetic_nip("200000000")

SELLER_NAME = "Przykladowy Dostawca Sp. z o.o."
BUYER_NAME = "Testowy Nabywca Jednoosobowa"


@dataclass
class Position:
    """One invoice line item, with money derived rather than hand-typed."""

    name: str
    unit: str = "szt."
    quantity: str = "1"
    unit_net: str = "100.00"
    vat_rate: str = "23"

    @property
    def net(self) -> Decimal:
        value = Decimal(self.quantity) * Decimal(self.unit_net)
        return value.quantize(Decimal("0.01"))

    @property
    def vat(self) -> Decimal:
        """VAT for this line, or zero when the rate is a code such as 'zw'."""
        try:
            rate = Decimal(self.vat_rate)
        except (ArithmeticError, ValueError):
            return Decimal("0.00")
        return (self.net * rate / Decimal(100)).quantize(Decimal("0.01"))

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


def default_positions() -> List[Position]:
    """Two single-rate positions summing to a tidy 200.00 net / 246.00 gross."""
    return [
        Position(name="Usluga hostingu", unit_net="100.00"),
        Position(name="Domena .pl", quantity="2", unit_net="50.00"),
    ]


def mixed_rate_positions() -> List[Position]:
    """Positions spanning two VAT rates, to exercise per-rate summing."""
    return [
        Position(name="Usluga IT", unit_net="100.00", vat_rate="23"),
        Position(name="Ksiazka techniczna", unit_net="200.00", vat_rate="5"),
    ]


def _rate_groups(positions: List[Position]) -> List[Dict[str, Decimal]]:
    """
    Group positions by VAT rate, preserving order of first appearance.

    FA(3) reports net and VAT totals per rate in numbered field pairs
    (``P_13_1``/``P_14_1``, ``P_13_2``/``P_14_2``, ...). The index ordering here
    only needs to be stable, since consumers sum across all of them.
    """
    groups: List[Dict[str, Decimal]] = []
    index: Dict[str, int] = {}
    for position in positions:
        if position.vat_rate not in index:
            index[position.vat_rate] = len(groups)
            groups.append(
                {"rate": position.vat_rate, "net": Decimal("0.00"), "vat": Decimal("0.00")}
            )
        group = groups[index[position.vat_rate]]
        group["net"] += position.net
        group["vat"] += position.vat
    return groups


def build_fa3_xml(
    ksef_number: str = "1000000000-20260625-AAAAAAAAAAAA-01",
    invoice_number: str = "FS 1/2026",
    issue_date: str = "2026-06-25",
    seller_name: str = SELLER_NAME,
    seller_nip: str = SELLER_NIP,
    buyer_name: str = BUYER_NAME,
    buyer_nip: str = BUYER_NIP,
    currency: str = "PLN",
    positions: Optional[List[Position]] = None,
    prefix: str = "",
    include_ksef_number: bool = True,
    include_rate_totals: bool = True,
) -> bytes:
    """
    Build a minimal FA(3) invoice document as UTF-8 bytes.

    Args:
        prefix: Namespace prefix to apply to every element, e.g. ``"tns:"``.
            Used to prove parsing is namespace-agnostic.
        include_ksef_number: Omit the KSeF reference element entirely.
        include_rate_totals: Omit the per-rate P_13_x/P_14_x pairs, forcing
            consumers onto the gross-minus-net fallback.
    """
    if positions is None:
        positions = default_positions()

    p = prefix
    groups = _rate_groups(positions)
    net_total = sum((g["net"] for g in groups), Decimal("0.00"))
    vat_total = sum((g["vat"] for g in groups), Decimal("0.00"))
    gross_total = net_total + vat_total

    ns = ' xmlns:tns="http://crd.gov.pl/wzor/2026/01/01/00000/"' if prefix else ""

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f"<{p}Faktura{ns}>",
    ]

    # Kept near the top of the document: the production parser only decodes the
    # first 8 KiB when looking for this element.
    if include_ksef_number:
        lines.append(f"  <{p}NumerKSeFDokumentu>{ksef_number}</{p}NumerKSeFDokumentu>")

    lines += [
        f"  <{p}Naglowek>",
        f'    <{p}KodFormularza kodSystemowy="FA (3)" wersjaSchemy="1-0E">FA</{p}KodFormularza>',
        f"    <{p}WariantFormularza>3</{p}WariantFormularza>",
        f"  </{p}Naglowek>",
        f"  <{p}Podmiot1>",
        f"    <{p}DaneIdentyfikacyjne>",
        f"      <{p}NIP>{seller_nip}</{p}NIP>",
        f"      <{p}Nazwa>{seller_name}</{p}Nazwa>",
        f"    </{p}DaneIdentyfikacyjne>",
        f"  </{p}Podmiot1>",
        f"  <{p}Podmiot2>",
        f"    <{p}DaneIdentyfikacyjne>",
        f"      <{p}NIP>{buyer_nip}</{p}NIP>",
        f"      <{p}Nazwa>{buyer_name}</{p}Nazwa>",
        f"    </{p}DaneIdentyfikacyjne>",
        f"  </{p}Podmiot2>",
        f"  <{p}Fa>",
        f"    <{p}KodWaluty>{currency}</{p}KodWaluty>",
        f"    <{p}P_1>{issue_date}</{p}P_1>",
        f"    <{p}P_2>{invoice_number}</{p}P_2>",
    ]

    if include_rate_totals:
        for i, group in enumerate(groups, start=1):
            lines.append(f"    <{p}P_13_{i}>{group['net']}</{p}P_13_{i}>")
            lines.append(f"    <{p}P_14_{i}>{group['vat']}</{p}P_14_{i}>")

    lines.append(f"    <{p}P_15>{gross_total}</{p}P_15>")

    for number, position in enumerate(positions, start=1):
        lines += [
            f"    <{p}FaWiersz>",
            f"      <{p}NrWierszaFa>{number}</{p}NrWierszaFa>",
            f"      <{p}P_7>{position.name}</{p}P_7>",
            f"      <{p}P_8A>{position.unit}</{p}P_8A>",
            f"      <{p}P_8B>{position.quantity}</{p}P_8B>",
            f"      <{p}P_9A>{position.unit_net}</{p}P_9A>",
            f"      <{p}P_11>{position.net}</{p}P_11>",
            f"      <{p}P_12>{position.vat_rate}</{p}P_12>",
            f"    </{p}FaWiersz>",
        ]

    lines += [f"  </{p}Fa>", f"</{p}Faktura>", ""]
    return "\n".join(lines).encode("utf-8")


def build_text_pdf(text: str) -> bytes:
    """Build a single-page PDF whose extracted text contains ``text``."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(0, 10, text)
    return bytes(pdf.output())
