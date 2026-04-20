#!/usr/bin/env python3
"""KSeF (Krajowy System e-Faktur) client for downloading invoices."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ksef2 import Client, Environment
from ksef2.domain.models.pagination import InvoiceMetadataParams
from ksef2.services.invoices import InvoicesFilter
from ksef_pdf_renderer import render_invoice_pdf

# KSeF API caps page_size at 100. Using the max keeps round-trips low while
# still paginating for months that exceed it.
_KSEF_PAGE_SIZE = 100


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

        # Query metadata across all pages. Default page_size is 10, so a naive
        # single-page read silently truncates any month with >10 invoices.
        invoices_meta = []
        page_offset = 0
        while True:
            params = InvoiceMetadataParams(
                page_size=_KSEF_PAGE_SIZE,
                page_offset=page_offset,
            )
            response = self._auth_client.invoices.query_metadata(
                filters=filters, params=params
            )
            invoices_meta.extend(response.invoices)
            self.logger.info(
                f"Fetched page {page_offset + 1}: {len(response.invoices)} invoice(s) "
                f"(total so far: {len(invoices_meta)}, has_more={response.has_more})"
            )
            if not response.has_more:
                break
            page_offset += 1

        self.logger.info(f"Found {len(invoices_meta)} invoices in KSeF")

        # Download each invoice. We must be resilient to missing metadata
        # fields — any invoice KSeF returns must end up in the result set.
        results: List[KSeFInvoice] = []
        for meta in invoices_meta:
            ksef_number = getattr(meta, "ksef_number", None) or "Unknown"
            try:
                xml_content = self._auth_client.invoices.download_invoice(
                    ksef_number=ksef_number
                )
            except Exception as e:
                self.logger.error(f"Failed to download invoice {ksef_number}: {e}")
                continue

            seller_name = self._safe_attr(meta, "seller", "name", default="Unknown")
            seller_nip = self._safe_attr(meta, "seller", "nip", default="Unknown")
            buyer_name = self._safe_attr(meta, "buyer", "name", default="Unknown")
            buyer_nip = self._safe_attr(
                meta, "buyer", "identifier", "value", default="Unknown"
            )

            try:
                issue_date = meta.issue_date.strftime("%Y-%m-%d")
            except AttributeError:
                issue_date = str(getattr(meta, "issue_date", "Unknown"))

            invoice = KSeFInvoice(
                ksef_number=ksef_number,
                invoice_number=getattr(meta, "invoice_number", "Unknown"),
                issue_date=issue_date,
                seller_name=seller_name,
                seller_nip=seller_nip,
                buyer_name=buyer_name,
                buyer_nip=buyer_nip,
                net_amount=getattr(meta, "net_amount", 0.0),
                gross_amount=getattr(meta, "gross_amount", 0.0),
                currency=getattr(meta, "currency", ""),
                xml_content=xml_content,
                invoice_type=str(getattr(meta, "invoice_type", "")),
            )
            results.append(invoice)

            self.logger.info(
                f"Downloaded: {invoice.invoice_number} from {seller_name} "
                f"({invoice.gross_amount} {invoice.currency})"
            )

        if len(results) != len(invoices_meta):
            self.logger.warning(
                f"Download gap: KSeF returned {len(invoices_meta)} invoices, "
                f"downloaded {len(results)}."
            )

        return results

    @staticmethod
    def _safe_attr(obj, *path: str, default=None):
        """Walk nested attributes defensively, returning default on any miss."""
        current = obj
        for name in path:
            if current is None:
                return default
            current = getattr(current, name, None)
        return current if current is not None else default

    def save_invoices(
        self,
        invoices: List[KSeFInvoice],
        output_directory: str,
    ) -> List[str]:
        """
        Save downloaded KSeF invoices as XML files, organized by month.

        Every invoice returned by KSeF for the queried month is saved. If a
        target filename already exists, a numeric suffix is appended so the
        existing file is never overwritten and the new invoice is never lost.

        Args:
            invoices: List of KSeF invoices to save
            output_directory: Base output directory

        Returns:
            List of saved file paths
        """
        saved_paths: List[str] = []

        for invoice in invoices:
            # Organize by month: output_directory/YYYY-MM/
            year_month = invoice.issue_date[:7]  # "YYYY-MM" from "YYYY-MM-DD"
            month_dir = os.path.join(output_directory, year_month)
            os.makedirs(month_dir, exist_ok=True)

            safe_seller = self._sanitize(invoice.seller_name)
            safe_number = self._sanitize(invoice.invoice_number)

            filename = f"{invoice.issue_date}, {safe_seller}, {safe_number}, ksef.pdf"
            file_path = self._unique_path(os.path.join(month_dir, filename))

            # Save XML alongside the PDF, sharing the same base name.
            xml_path = os.path.splitext(file_path)[0] + ".xml"
            xml_path = self._unique_path(xml_path)
            with open(xml_path, "wb") as f:
                f.write(invoice.xml_content)

            # Render XML to PDF. Rendering failures do NOT block the XML save.
            try:
                pdf_bytes = render_invoice_pdf(
                    invoice.xml_content,
                    ksef_number=invoice.ksef_number,
                    seller_nip=invoice.seller_nip,
                )
                with open(file_path, "wb") as f:
                    f.write(pdf_bytes)
            except Exception as e:
                self.logger.warning(
                    f"PDF render failed for {invoice.invoice_number} "
                    f"(seller NIP {invoice.seller_nip}): {e}. XML kept at {xml_path}."
                )

            saved_paths.append(file_path)
            self.logger.info(f"Saved: {os.path.basename(file_path)}")

        if len(saved_paths) != len(invoices):
            self.logger.warning(
                f"Save gap: attempted to save {len(invoices)} invoices, "
                f"saved {len(saved_paths)} files."
            )

        return saved_paths

    @staticmethod
    def _unique_path(path: str) -> str:
        """Append _1, _2, ... to avoid overwriting an existing file."""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        counter = 1
        candidate = f"{base}_{counter}{ext}"
        while os.path.exists(candidate):
            counter += 1
            candidate = f"{base}_{counter}{ext}"
        return candidate

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove invalid filename characters."""
        invalid_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
        for char in invalid_chars:
            text = text.replace(char, "_")
        return text[:100]
