#!/usr/bin/env python3
"""KSeF (Krajowy System e-Faktur) client for downloading invoices."""

import io
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, TypeVar

from ksef2 import Client, Environment
from ksef2.core.exceptions import KSeFRateLimitError
from ksef2.domain.models.pagination import InvoiceMetadataParams
from ksef2.services.invoices import InvoicesFilter
from ksef_pdf_renderer import render_invoice_pdf

# KSeF API caps page_size at 100. Using the max keeps round-trips low while
# still paginating for months that exceed it.
_KSEF_PAGE_SIZE = 100

# KSeF's download endpoint has a small burst bucket that, once tripped, returns
# a Retry-After of many minutes. Pace proactively well below the documented
# 16 req/min to avoid tripping it at all. Reactive 429 handling is a safety net.
_MIN_REQUEST_INTERVAL_SECONDS = 7.0
_RATE_LIMIT_BUFFER_SECONDS = 2
_RATE_LIMIT_FALLBACK_SECONDS = 60
# Cap reactive sleep — KSeF sometimes returns Retry-After in the tens of
# minutes; waiting that long blocks the run for no real benefit, since the
# proactive spacing above should already keep us under the limit.
_RATE_LIMIT_MAX_WAIT_SECONDS = 120
_RATE_LIMIT_MAX_RETRIES = 5

T = TypeVar("T")


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
        self._last_request_time: float = 0.0

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

    def _throttle(self) -> None:
        """Sleep so consecutive API calls stay ``_MIN_REQUEST_INTERVAL_SECONDS`` apart."""
        elapsed = time.monotonic() - self._last_request_time
        wait = _MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)

    def _call_with_rate_limit_retry(self, description: str, func: Callable[[], T]) -> T:
        """
        Invoke a KSeF API call with proactive pacing and reactive 429 retry.

        Proactive: enforces a minimum spacing between consecutive requests so
        bursts don't saturate the 16 req/min bucket.

        Reactive: on 429, sleeps ``retry_after`` seconds (plus a buffer) and
        retries up to ``_RATE_LIMIT_MAX_RETRIES`` times.
        """
        attempt = 0
        while True:
            self._throttle()
            try:
                result = func()
                self._last_request_time = time.monotonic()
                return result
            except KSeFRateLimitError as e:
                self._last_request_time = time.monotonic()
                attempt += 1
                if attempt > _RATE_LIMIT_MAX_RETRIES:
                    raise
                raw_wait = (
                    e.retry_after
                    if e.retry_after is not None
                    else _RATE_LIMIT_FALLBACK_SECONDS
                ) + _RATE_LIMIT_BUFFER_SECONDS
                wait_seconds = min(raw_wait, _RATE_LIMIT_MAX_WAIT_SECONDS)
                self.logger.warning(
                    f"Rate limited on {description} (attempt {attempt}); "
                    f"sleeping {wait_seconds}s before retry."
                )
                time.sleep(wait_seconds)

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

        # Filter by legal issue date ("data wystawienia"), not KSeF acceptance
        # date. An invoice issued 30 Apr but submitted on 5 May belongs to the
        # April accounting period.
        filters = InvoicesFilter(
            role=role,
            date_type="issue_date",
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
            response = self._call_with_rate_limit_retry(
                f"query_metadata (page_offset={page_offset})",
                lambda: self._auth_client.invoices.query_metadata(
                    filters=filters, params=params
                ),
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
                xml_content = self._call_with_rate_limit_retry(
                    f"download_invoice {ksef_number}",
                    lambda kn=ksef_number: self._auth_client.invoices.download_invoice(
                        ksef_number=kn
                    ),
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

    def bulk_export(
        self,
        role: str = "buyer",
        month: Optional[str] = None,
        timeout: float = 600.0,
        poll_interval: float = 5.0,
    ) -> List[KSeFInvoice]:
        """
        Download all invoices for a month via KSeF's async bulk export.

        The bulk export is one job (schedule → poll → download zip(s)) rather
        than one HTTP call per invoice, so it bypasses the per-invoice rate
        limit entirely. Metadata is still fetched via the paginated query so
        seller/buyer/amount fields are populated identically to query_invoices.
        """
        self._ensure_authenticated()

        date_from, date_to, month_str = self._month_range(month)
        self.logger.info(
            f"Bulk-exporting KSeF invoices (role={role}, month={month_str})..."
        )

        filters = InvoicesFilter(
            role=role,
            date_type="issue_date",
            date_from=date_from,
            date_to=date_to,
            amount_type="brutto",
        )

        invoices_meta = self._fetch_all_metadata(filters)
        if not invoices_meta:
            self.logger.info("No invoices to export.")
            return []

        self.logger.info(
            f"Scheduling bulk export for {len(invoices_meta)} invoice(s)..."
        )
        package_blobs: List[bytes] = self._auth_client.invoices.export_and_download(
            filters=filters,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        self.logger.info(
            f"Bulk export downloaded {len(package_blobs)} package part(s); "
            f"extracting XMLs..."
        )

        xml_by_ksef = self._extract_xmls_by_ksef_number(package_blobs)
        self.logger.info(f"Extracted {len(xml_by_ksef)} XML invoice(s) from package.")

        return self._merge_metadata_and_xml(invoices_meta, xml_by_ksef)

    def _month_range(self, month: Optional[str]):
        """Return (date_from, date_to, month_str) for a YYYY-MM month."""
        if month:
            year, mon = month.split("-")
            date_from = datetime(int(year), int(mon), 1)
        else:
            now = datetime.now()
            date_from = datetime(now.year, now.month, 1)

        if date_from.month == 12:
            date_to = datetime(date_from.year + 1, 1, 1)
        else:
            date_to = datetime(date_from.year, date_from.month + 1, 1)

        return date_from, date_to, date_from.strftime("%Y-%m")

    def _fetch_all_metadata(self, filters: InvoicesFilter) -> list:
        """Paginate through query_metadata and return all InvoiceMetadata items."""
        invoices_meta: list = []
        page_offset = 0
        while True:
            response = self._call_with_rate_limit_retry(
                f"query_metadata page {page_offset + 1}",
                lambda: self._auth_client.invoices.query_metadata(
                    filters=filters,
                    params=InvoiceMetadataParams(
                        page_offset=page_offset,
                        page_size=_KSEF_PAGE_SIZE,
                    ),
                ),
            )
            invoices_meta.extend(response.invoices)
            if not response.has_more:
                break
            page_offset += 1
        return invoices_meta

    @staticmethod
    def _extract_xmls_by_ksef_number(package_blobs: List[bytes]) -> Dict[str, bytes]:
        """Unzip each decrypted package blob and key entries by KSeF number.

        Each blob is a ZIP whose entries are individual invoice XMLs. The KSeF
        number is parsed from the XML's ``<KodFormularza>``/``<NumerKSeFDokumentu>``
        region; we fall back to the zip entry's filename if parsing fails.
        """
        xml_by_ksef: Dict[str, bytes] = {}
        for blob in package_blobs:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith(".xml"):
                        continue
                    data = zf.read(name)
                    ksef_number = (
                        KSeFClient._extract_ksef_number_from_xml(data)
                        or os.path.splitext(os.path.basename(name))[0]
                    )
                    xml_by_ksef[ksef_number] = data
        return xml_by_ksef

    @staticmethod
    def _extract_ksef_number_from_xml(xml_bytes: bytes) -> Optional[str]:
        """Parse the KSeF reference number out of an FA(3) XML, namespace-agnostic."""
        text = xml_bytes[:8192].decode("utf-8", errors="ignore")
        match = re.search(
            r"<(?:[\w-]+:)?NumerKSeFDokumentu>\s*([^<\s]+)\s*</",
            text,
        )
        return match.group(1) if match else None

    def _merge_metadata_and_xml(
        self,
        invoices_meta: list,
        xml_by_ksef: Dict[str, bytes],
    ) -> List[KSeFInvoice]:
        """Build KSeFInvoice objects by joining metadata to extracted XML bytes."""
        results: List[KSeFInvoice] = []
        for meta in invoices_meta:
            ksef_number = getattr(meta, "ksef_number", None) or "Unknown"
            xml_content = xml_by_ksef.get(ksef_number)
            if xml_content is None:
                self.logger.warning(
                    f"Bulk export missing XML for {ksef_number}; skipping."
                )
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
                f"Bulk: {invoice.invoice_number} from {seller_name} "
                f"({invoice.gross_amount} {invoice.currency})"
            )

        if len(results) != len(invoices_meta):
            self.logger.warning(
                f"Bulk export gap: metadata returned {len(invoices_meta)} "
                f"invoices, matched {len(results)} XMLs."
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
