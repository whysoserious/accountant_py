#!/usr/bin/env python3
"""KSeF (Krajowy System e-Faktur) client for downloading invoices."""

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Set
from xml.etree import ElementTree

from ksef2 import Client, Environment
from ksef2.services.invoices import InvoicesFilter
from ksef_pdf_renderer import render_invoice_pdf


@dataclass
class KSeFConfig:
    """Configuration for KSeF connection."""

    nip: str
    token: str
    environment: str = "production"


@dataclass
class KSeFInvoice:
    """Metadata for a downloaded KSeF invoice."""

    ksef_number: str
    invoice_number: str
    issue_date: str
    seller_name: str
    seller_nip: str
    buyer_name: str
    buyer_nip: str
    net_amount: float
    gross_amount: float
    currency: str
    xml_content: bytes
    invoice_type: str


class KSeFClient:
    """Client for downloading invoices from KSeF."""

    ENVIRONMENT_MAP = {
        "production": Environment.PRODUCTION,
        "test": Environment.TEST,
        "demo": Environment.DEMO,
    }

    def __init__(self, config: KSeFConfig, logger: logging.Logger):
        """
        Initialize KSeF client.

        Args:
            config: KSeF connection configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        self._client: Optional[Client] = None
        self._auth_client = None

    def connect(self) -> None:
        """
        Authenticate with KSeF.

        Raises:
            ConnectionError: If connection fails
            PermissionError: If authentication fails
        """
        env_name = self.config.environment.lower()
        env = self.ENVIRONMENT_MAP.get(env_name)
        if not env:
            raise ValueError(
                f"Unknown KSeF environment: '{env_name}'. "
                f"Use: {', '.join(self.ENVIRONMENT_MAP.keys())}"
            )

        self.logger.info(f"Connecting to KSeF ({env_name})...")

        try:
            self._client = Client(environment=env)
            self._auth_client = self._client.authentication.with_token(
                ksef_token=self.config.token,
                nip=self.config.nip,
            )
            self.logger.info(f"Authenticated with KSeF for NIP {self.config.nip}")
        except Exception as e:
            raise ConnectionError(f"KSeF authentication failed: {e}")

    def disconnect(self) -> None:
        """Close the KSeF connection."""
        if self._client:
            try:
                self._client.close()
                self.logger.info("Disconnected from KSeF")
            except Exception:
                pass
            finally:
                self._client = None
                self._auth_client = None

    def _ensure_authenticated(self) -> None:
        """Ensure we have an active authenticated session."""
        if not self._auth_client:
            raise RuntimeError("Not authenticated. Call connect() first.")

    def query_invoices(
        self,
        role: str = "buyer",
        month: Optional[str] = None,
    ) -> List[KSeFInvoice]:
        """
        Query and download invoices from KSeF for a specific month.

        Args:
            role: Invoice role - 'buyer' for purchase invoices,
                  'seller' for sales invoices
            month: Month in YYYY-MM format (defaults to current month)

        Returns:
            List of KSeFInvoice objects with XML content
        """
        self._ensure_authenticated()

        if month:
            year, mon = month.split("-")
            date_from = datetime(int(year), int(mon), 1)
        else:
            now = datetime.now()
            date_from = datetime(now.year, now.month, 1)

        # Calculate end of month
        if date_from.month == 12:
            date_to = datetime(date_from.year + 1, 1, 1)
        else:
            date_to = datetime(date_from.year, date_from.month + 1, 1)

        month_str = date_from.strftime("%Y-%m")
        self.logger.info(f"Querying KSeF invoices (role={role}, month={month_str})...")

        filters = InvoicesFilter(
            role=role,
            date_type="invoicing_date",
            date_from=date_from,
            date_to=date_to,
            amount_type="brutto",
        )

        # Query metadata
        response = self._auth_client.invoices.query_metadata(filters=filters)
        invoices_meta = response.invoices

        self.logger.info(f"Found {len(invoices_meta)} invoices in KSeF")

        # Download each invoice
        results: List[KSeFInvoice] = []
        for meta in invoices_meta:
            try:
                xml_content = self._auth_client.invoices.download_invoice(
                    ksef_number=meta.ksef_number
                )

                seller_name = meta.seller.name if meta.seller else "Unknown"
                seller_nip = meta.seller.nip if meta.seller else "Unknown"
                buyer_name = meta.buyer.name if meta.buyer else "Unknown"
                buyer_nip = (
                    meta.buyer.identifier.value
                    if meta.buyer and meta.buyer.identifier
                    else "Unknown"
                )

                invoice = KSeFInvoice(
                    ksef_number=meta.ksef_number,
                    invoice_number=meta.invoice_number,
                    issue_date=meta.issue_date.strftime("%Y-%m-%d"),
                    seller_name=seller_name,
                    seller_nip=seller_nip,
                    buyer_name=buyer_name,
                    buyer_nip=buyer_nip,
                    net_amount=meta.net_amount,
                    gross_amount=meta.gross_amount,
                    currency=meta.currency,
                    xml_content=xml_content,
                    invoice_type=str(meta.invoice_type),
                )
                results.append(invoice)

                self.logger.info(
                    f"Downloaded: {meta.invoice_number} from {seller_name} "
                    f"({meta.gross_amount} {meta.currency})"
                )

            except Exception as e:
                self.logger.error(f"Failed to download invoice {meta.ksef_number}: {e}")

        return results

    def save_invoices(
        self,
        invoices: List[KSeFInvoice],
        output_directory: str,
    ) -> List[str]:
        """
        Save downloaded KSeF invoices as XML files, organized by month.

        Files are saved to output_directory/YYYY-MM/ based on invoice date.

        Args:
            invoices: List of KSeF invoices to save
            output_directory: Base output directory

        Returns:
            List of saved file paths
        """
        saved_paths: List[str] = []

        # Build dedup index: scan existing invoice numbers in output tree
        known_numbers: Set[str] = set()
        for root, _, files in os.walk(output_directory):
            for f in files:
                parts = f.rsplit(".", 1)[0].split(", ")
                if len(parts) >= 3:
                    known_numbers.add(parts[2].strip().lower())

        for invoice in invoices:
            # Organize by month: output_directory/YYYY-MM/
            year_month = invoice.issue_date[:7]  # "YYYY-MM" from "YYYY-MM-DD"
            month_dir = os.path.join(output_directory, year_month)
            os.makedirs(month_dir, exist_ok=True)

            # Check for duplicate by invoice number
            safe_seller = self._sanitize(invoice.seller_name)
            safe_number = self._sanitize(invoice.invoice_number)
            if safe_number.lower() in known_numbers:
                self.logger.info(
                    f"Skipping duplicate: {invoice.invoice_number} "
                    f"from {invoice.seller_name}"
                )
                continue

            filename = (
                f"{invoice.issue_date}, {safe_seller}, " f"{safe_number}, ksef.pdf"
            )

            file_path = os.path.join(month_dir, filename)

            # Handle duplicates
            if os.path.exists(file_path):
                base, ext = os.path.splitext(file_path)
                counter = 1
                while os.path.exists(file_path):
                    file_path = f"{base}_{counter}{ext}"
                    counter += 1

            # Save XML
            xml_path = file_path.replace(".pdf", ".xml")
            with open(xml_path, "wb") as f:
                f.write(invoice.xml_content)

            # Render XML to PDF
            try:
                pdf_bytes = render_invoice_pdf(invoice.xml_content)
                with open(file_path, "wb") as f:
                    f.write(pdf_bytes)
            except Exception as e:
                self.logger.warning(
                    f"PDF render failed for {invoice.invoice_number}: {e}"
                )

            saved_paths.append(file_path)
            known_numbers.add(safe_number.lower())
            self.logger.info(f"Saved: {filename}")

        return saved_paths

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove invalid filename characters."""
        invalid_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
        for char in invalid_chars:
            text = text.replace(char, "_")
        return text[:100]
