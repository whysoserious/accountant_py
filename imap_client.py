#!/usr/bin/env python3
"""IMAP client for searching and downloading email attachments."""
import imaplib
import email
from email.message import Message
import os
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from typing import List, Tuple, Optional
from dataclasses import dataclass
from config_parser import MailboxConfig, SearchConfig
import logging
from constants import GMAIL_FOLDERS, VALID_FILE_EXTENSIONS, VALID_CONTENT_TYPES


@dataclass
class EmailAttachment:
    """Represents an email attachment."""

    filename: str
    content: bytes
    content_type: str
    email_subject: str
    email_from: str
    email_date: datetime


@dataclass
class EmailSearchResult:
    """Result of email search operation."""

    mailbox_name: str
    total_emails_searched: int
    emails_with_attachments: int
    attachments_found: List[EmailAttachment]


class IMAPClient:
    """Client for interacting with IMAP mailboxes."""

    def __init__(self, mailbox_config: MailboxConfig, logger: logging.Logger):
        """
        Initialize IMAP client.

        Args:
            mailbox_config: Configuration for the mailbox
            logger: Logger instance for logging operations
        """
        self.config = mailbox_config
        self.logger = logger
        self.connection: Optional[imaplib.IMAP4_SSL] = None

    def connect(self) -> None:
        """
        Connect to the IMAP server.

        Raises:
            imaplib.IMAP4.error: If connection fails
        """
        try:
            self.logger.info(
                f"Connecting to {self.config.host}:{self.config.port} "
                f"for {self.config.username}..."
            )

            if self.config.use_ssl:
                self.connection = imaplib.IMAP4_SSL(
                    self.config.host, self.config.port
                )
            else:
                self.connection = imaplib.IMAP4(self.config.host, self.config.port)

            self.connection.login(self.config.username, self.config.password)
            self.logger.info(f"Successfully connected to {self.config.name}")

        except imaplib.IMAP4.error as e:
            self.logger.error(f"Failed to connect to {self.config.name}: {e}")
            raise

    def disconnect(self) -> None:
        """Disconnect from the IMAP server."""
        if self.connection:
            try:
                self.connection.logout()
                self.logger.info(f"Disconnected from {self.config.name}")
            except Exception as e:
                self.logger.warning(f"Error during disconnect: {e}")
            finally:
                self.connection = None

    def _is_gmail(self) -> bool:
        """Check if the current mailbox is Gmail."""
        return "gmail" in self.config.host.lower()

    def _select_mailbox_folder(self) -> Optional[str]:
        """
        Select the appropriate mailbox folder (INBOX, All Mail, etc.).

        Returns:
            Name of selected folder or None if selection failed
        """
        selected_folder = None

        if self._is_gmail():
            # List available folders for debugging
            self._log_available_folders()

            # Try Gmail-specific folders
            for folder_name in GMAIL_FOLDERS:
                try:
                    self.logger.debug(f"Trying Gmail folder: {folder_name}")
                    status, data = self.connection.select(folder_name)
                    if status == "OK":
                        selected_folder = folder_name.strip('"')
                        self.logger.info(f"Selected folder: {selected_folder} (Gmail)")
                        break
                except Exception as e:
                    self.logger.debug(f"Could not select {folder_name}: {e}")
                    continue

        if not selected_folder:
            # Standard INBOX for other providers or fallback
            try:
                status, data = self.connection.select("INBOX")
                if status == "OK":
                    selected_folder = "INBOX"
                    self.logger.info(f"Selected folder: {selected_folder}")
            except Exception:
                pass

        if not selected_folder:
            self.logger.error("Could not select any mailbox folder")

        return selected_folder

    def _log_available_folders(self) -> None:
        """Log available mailbox folders for debugging."""
        try:
            self.logger.debug("Listing available folders...")
            status, folders = self.connection.list()
            if status == "OK":
                for folder_info in folders:
                    if folder_info:
                        try:
                            folder_str = folder_info.decode('utf-8')
                            self.logger.debug(f"Available folder: {folder_str}")
                        except Exception:
                            pass
        except Exception as e:
            self.logger.debug(f"Could not list folders: {e}")

    def _format_imap_date(self, date: datetime) -> str:
        """
        Format date for IMAP SINCE command.

        Args:
            date: Date to format

        Returns:
            IMAP-formatted date string (e.g., "1-Oct-2025")
        """
        day = date.day
        month = date.strftime("%b")
        year = date.year
        return f"{day}-{month}-{year}"

    def _build_gmail_query(self, since_date: datetime, keywords: List[str], with_attachments: bool = True) -> str:
        """
        Build Gmail X-GM-RAW query string.

        Args:
            since_date: Date to search from
            keywords: Keywords to search for
            with_attachments: Whether to filter for attachments

        Returns:
            Gmail query string
        """
        # Format date as YYYY/M/D
        gmail_date = f"{since_date.year}/{since_date.month}/{since_date.day}"

        # Build keyword query - only use ASCII-safe keywords
        safe_keywords = []
        for kw in keywords:
            try:
                kw.encode('ascii')
                safe_keywords.append(kw)
            except UnicodeEncodeError:
                self.logger.debug(f"Skipping non-ASCII keyword: {kw}")

        if not safe_keywords:
            return ""

        keyword_parts = " OR ".join(safe_keywords)

        # Build query
        query_parts = [f'after:{gmail_date}']
        if with_attachments:
            query_parts.append('has:attachment')
        query_parts.append(f'({keyword_parts})')

        return ' '.join(query_parts)

    def _search_gmail(self, search_config: SearchConfig, since_date: datetime) -> Set[bytes]:
        """
        Search Gmail using X-GM-RAW extension.

        Args:
            search_config: Search configuration
            since_date: Date to search from

        Returns:
            Set of email IDs
        """
        all_email_ids = set()

        # Build Gmail query with attachments
        gmail_query = self._build_gmail_query(since_date, search_config.keywords, with_attachments=True)

        if not gmail_query:
            self.logger.warning("No ASCII-safe keywords for Gmail search")
            return all_email_ids

        self.logger.info(f"🔍 Gmail X-GM-RAW query: {gmail_query}")

        try:
            search_string = f'X-GM-RAW "{gmail_query}"'
            self.logger.info(f"Exact IMAP command: SEARCH {search_string}")
            status, messages = self.connection.search(None, search_string)

            if status == "OK" and messages[0]:
                email_ids = messages[0].split()
                all_email_ids.update(email_ids)
                self.logger.info(f"📧 Gmail X-GM-RAW found {len(email_ids)} emails")
                return all_email_ids
            else:
                self.logger.warning(f"⚠️ Gmail X-GM-RAW returned 0 results")

                # Try alternative date formats
                all_email_ids.update(self._try_alternative_gmail_formats(search_config, since_date))

        except Exception as e:
            self.logger.error(f"X-GM-RAW failed: {e}")

        return all_email_ids

    def _try_alternative_gmail_formats(self, search_config: SearchConfig, since_date: datetime) -> Set[bytes]:
        """
        Try alternative Gmail query formats if the primary format fails.

        Args:
            search_config: Search configuration
            since_date: Date to search from

        Returns:
            Set of email IDs
        """
        all_email_ids = set()
        safe_keywords = [kw for kw in search_config.keywords if self._is_ascii_safe(kw)]

        if not safe_keywords:
            return all_email_ids

        keyword_parts = " OR ".join(safe_keywords)

        # Try M/D/YYYY format
        gmail_date_mdy = f"{since_date.month}/{since_date.day}/{since_date.year}"
        queries_to_try = [
            f'after:{gmail_date_mdy} has:attachment ({keyword_parts})',
            f'newer_than:{search_config.days_back}d has:attachment ({keyword_parts})',
            f'has:attachment ({keyword_parts})'  # Without date as last resort
        ]

        for query in queries_to_try:
            try:
                self.logger.info(f"Trying alternative query: {query}")
                search_string = f'X-GM-RAW "{query}"'
                status, messages = self.connection.search(None, search_string)

                if status == "OK" and messages[0]:
                    email_ids = messages[0].split()
                    all_email_ids.update(email_ids)
                    self.logger.info(f"Alternative query found {len(email_ids)} emails")
                    break
            except Exception as e:
                self.logger.debug(f"Alternative query failed: {e}")

        return all_email_ids

    def _is_ascii_safe(self, text: str) -> bool:
        """Check if text is ASCII-safe."""
        try:
            text.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    def _search_standard_imap(self, search_config: SearchConfig, since_date: datetime) -> Set[bytes]:
        """
        Search using standard IMAP commands.

        Args:
            search_config: Search configuration
            since_date: Date to search from

        Returns:
            Set of email IDs
        """
        all_email_ids = set()
        since_date_str = self._format_imap_date(since_date)

        self.logger.info(f"Using standard IMAP SINCE {since_date_str} + keywords")

        for keyword in search_config.keywords:
            self.logger.info(f"Searching for keyword: '{keyword}'")

            try:
                search_query = f'SINCE {since_date_str} OR SUBJECT "{keyword}" BODY "{keyword}"'
                self.logger.debug(f"Exact IMAP command: SEARCH {search_query}")
                status, messages = self.connection.search(None, search_query)

                if status == "OK" and messages[0]:
                    email_ids = messages[0].split()
                    all_email_ids.update(email_ids)
                    self.logger.info(f"  Found {len(email_ids)} emails for '{keyword}'")
            except Exception as e:
                self.logger.warning(f"SINCE search failed for '{keyword}': {e}")

        return all_email_ids

    def _fetch_and_process_email(self, email_id: bytes, since_date: datetime,
                                  use_date_filter: bool) -> Optional[Tuple[str, Message]]:
        """
        Fetch and process a single email.

        Args:
            email_id: Email ID to fetch
            since_date: Date for filtering
            use_date_filter: Whether to apply date filtering

        Returns:
            Tuple of (email_id_str, email_message) or None if email should be skipped
        """
        try:
            # Fetch full email
            status, msg_data = self.connection.fetch(email_id, "(RFC822)")
            if status != "OK":
                return None

            # Parse email
            email_body = msg_data[0][1]
            email_message = email.message_from_bytes(email_body)

            # Get subject for logging
            subject = self._decode_header(email_message.get("Subject", "No Subject"))

            # Check date if filtering in Python
            if not use_date_filter:
                if not self._is_email_within_date_range(email_message, since_date, subject):
                    return None

            # Check for PDF attachments
            if not self._has_pdf_attachments(email_message):
                self.logger.debug(f"⏭️ Skipping email '{subject}' - No PDF attachments")
                return None

            # Get email date for logging
            date_display = self._get_email_date_display(email_message)

            # Log accepted email
            self.logger.info(
                f"✅ Accepting email: '{subject}' ({date_display}) - PDF attachments found"
            )

            # Convert email_id to string
            email_id_str = email_id.decode() if isinstance(email_id, bytes) else email_id
            return (email_id_str, email_message)

        except Exception as e:
            email_id_str = email_id.decode() if isinstance(email_id, bytes) else email_id
            self.logger.error(f"Error fetching email {email_id_str}: {e}")
            return None

    def _is_email_within_date_range(self, email_message: Message, since_date: datetime,
                                     subject: str) -> bool:
        """
        Check if email is within the specified date range.

        Args:
            email_message: Email message to check
            since_date: Date to compare against
            subject: Email subject for logging

        Returns:
            True if email is within range, False otherwise
        """
        date_str = email_message.get("Date", "")
        try:
            email_date = email.utils.parsedate_to_datetime(date_str)

            # Make since_date timezone-aware if needed
            if email_date.tzinfo is not None and since_date.tzinfo is None:
                since_date_aware = since_date.replace(tzinfo=timezone.utc)
            else:
                since_date_aware = since_date

            # Check if email is within our date range
            if email_date < since_date_aware:
                self.logger.debug(
                    f"Skipping email '{subject}' from {email_date.strftime('%Y-%m-%d')} "
                    f"(before {since_date_aware.strftime('%Y-%m-%d')})"
                )
                return False
            return True

        except Exception as e:
            self.logger.warning(f"Could not parse date for email '{subject}': {e}")
            return False

    def _has_pdf_attachments(self, email_message: Message) -> bool:
        """
        Check if email has PDF attachments.

        Args:
            email_message: Email message to check

        Returns:
            True if email has PDF attachments, False otherwise
        """
        for part in email_message.walk():
            if part.get_content_disposition() == "attachment":
                filename = part.get_filename()
                if filename and filename.lower().endswith('.pdf'):
                    return True
        return False

    def _get_email_date_display(self, email_message: Message) -> str:
        """
        Get formatted email date for display.

        Args:
            email_message: Email message

        Returns:
            Formatted date string
        """
        date_str = email_message.get("Date", "")
        try:
            email_date = email.utils.parsedate_to_datetime(date_str)
            return email_date.strftime('%Y-%m-%d')
        except Exception:
            return "Unknown date"

    def search_emails(self, search_config: SearchConfig) -> List[Tuple[str, Message]]:
        """
        Search for emails matching the search criteria.

        Args:
            search_config: Search configuration with keywords and date range

        Returns:
            List of tuples (email_id, email_message)
        """
        if not self.connection:
            raise RuntimeError("Not connected to IMAP server")

        # Select appropriate folder
        selected_folder = self._select_mailbox_folder()
        if not selected_folder:
            return []

        # Calculate date range
        since_date = datetime.now() - timedelta(days=search_config.days_back)
        self.logger.info(
            f"📅 Searching emails from last {search_config.days_back} days "
            f"(since {since_date.strftime('%Y-%m-%d')})..."
        )

        # Search emails based on provider
        if self._is_gmail():
            all_email_ids = self._search_gmail(search_config, since_date)
            use_date_filter = bool(all_email_ids)  # Gmail already filtered by date
        else:
            all_email_ids = self._search_standard_imap(search_config, since_date)
            use_date_filter = True  # Standard IMAP SINCE should work

        # Convert to list
        email_ids_list = list(all_email_ids)
        self.logger.info(f"Found {len(email_ids_list)} unique emails (date+keyword filtered)")

        # Fetch and process emails
        emails = []
        for email_id in email_ids_list:
            result = self._fetch_and_process_email(email_id, since_date, use_date_filter)
            if result:
                emails.append(result)

        self.logger.info(
            f"✨ Found {len(emails)} emails matching date and keyword criteria with PDF attachments"
        )
        return emails

    def extract_attachments(self, email_message: Message) -> List[EmailAttachment]:
        """
        Extract all attachments from an email message.

        Args:
            email_message: Email message to extract attachments from

        Returns:
            List of EmailAttachment objects
        """
        attachments = []

        # Get email metadata
        subject = self._decode_header(email_message.get("Subject", "No Subject"))
        from_addr = self._decode_header(email_message.get("From", "Unknown"))
        date_str = email_message.get("Date", "")

        # Parse date
        try:
            email_date = email.utils.parsedate_to_datetime(date_str)
        except Exception:
            email_date = datetime.now()

        # Iterate through email parts
        for part in email_message.walk():
            # Check if part is an attachment
            if part.get_content_maintype() == "multipart":
                continue

            if part.get("Content-Disposition") is None:
                continue

            filename = part.get_filename()
            if not filename:
                continue

            # Decode filename
            filename = self._decode_header(filename)

            # Get content type
            content_type = part.get_content_type()

            # Only process PDF and image files
            if not self._is_valid_attachment(filename, content_type):
                self.logger.debug(
                    f"Skipping attachment '{filename}' (type: {content_type})"
                )
                continue

            # Get attachment content
            try:
                content = part.get_payload(decode=True)
                if content:
                    attachment = EmailAttachment(
                        filename=filename,
                        content=content,
                        content_type=content_type,
                        email_subject=subject,
                        email_from=from_addr,
                        email_date=email_date,
                    )
                    attachments.append(attachment)
                    self.logger.debug(f"Extracted attachment: {filename}")
            except Exception as e:
                self.logger.error(f"Error extracting attachment '{filename}': {e}")

        return attachments

    def mark_as_read(self, email_id: str) -> None:
        """
        Mark an email as read.

        Args:
            email_id: Email ID to mark as read
        """
        if not self.connection:
            raise RuntimeError("Not connected to IMAP server")

        try:
            self.connection.store(email_id, "+FLAGS", "\\Seen")
            self.logger.debug(f"Marked email {email_id} as read")
        except Exception as e:
            self.logger.warning(f"Failed to mark email {email_id} as read: {e}")

    def _decode_header(self, header: Optional[str]) -> str:
        """Decode email header."""
        if not header:
            return ""

        decoded_parts = []
        for part, encoding in decode_header(header):
            if isinstance(part, bytes):
                try:
                    decoded_parts.append(
                        part.decode(encoding or "utf-8", errors="replace")
                    )
                except Exception:
                    decoded_parts.append(part.decode("utf-8", errors="replace"))
            else:
                decoded_parts.append(str(part))

        return "".join(decoded_parts)

    def _is_valid_attachment(self, filename: str, content_type: str) -> bool:
        """
        Check if attachment is a valid file type (PDF or image).

        Args:
            filename: Attachment filename
            content_type: MIME content type

        Returns:
            True if attachment should be processed, False otherwise
        """
        # Check file extension
        filename_lower = filename.lower()
        has_valid_extension = any(
            filename_lower.endswith(ext) for ext in VALID_FILE_EXTENSIONS
        )

        # Check content type
        has_valid_content_type = any(
            content_type.startswith(ct) for ct in VALID_CONTENT_TYPES
        )

        return has_valid_extension or has_valid_content_type

    def search_and_extract_attachments(
        self, search_config: SearchConfig
    ) -> EmailSearchResult:
        """
        Search emails and extract all attachments.

        Args:
            search_config: Search configuration

        Returns:
            EmailSearchResult with statistics and extracted attachments
        """
        emails = self.search_emails(search_config)
        all_attachments = []
        emails_with_attachments = 0

        for email_id, email_message in emails:
            attachments = self.extract_attachments(email_message)
            if attachments:
                all_attachments.extend(attachments)
                emails_with_attachments += 1

                # Mark email as read
                try:
                    self.mark_as_read(email_id)
                except Exception as e:
                    self.logger.warning(f"Failed to mark email as read: {e}")

        result = EmailSearchResult(
            mailbox_name=self.config.name,
            total_emails_searched=len(emails),
            emails_with_attachments=emails_with_attachments,
            attachments_found=all_attachments,
        )

        self.logger.info(
            f"Search complete for {self.config.name}: "
            f"{len(emails)} emails searched, "
            f"{emails_with_attachments} with attachments, "
            f"{len(all_attachments)} attachments extracted"
        )

        return result