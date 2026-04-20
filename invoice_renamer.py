#!/usr/bin/env python3
"""Invoice renaming functionality."""

import os
import hashlib
import shutil
import re
import json
import base64
import io
import logging
from typing import Dict, List, Set, Tuple, Optional
import PyPDF2
import anthropic
from pdf2image import convert_from_path
from PIL import Image
from constants import (
    CLAUDE_SONNET_MODEL,
    MAX_TOKENS_CATEGORIZATION,
    DEFAULT_TEMPERATURE,
    DEFAULT_DPI,
    DEFAULT_IMAGE_QUALITY,
    MAX_PDF_PAGES,
    MAX_FILENAME_LENGTH,
)


class InvoiceRenamer:
    """Handles invoice renaming operations."""

    # Default invoice data for error cases
    DEFAULT_INVOICE_DATA = {
        "date": "Unknown",
        "company": "Unknown",
        "invoice_number": "Unknown",
        "description": "Unknown",
    }

    def __init__(
        self, api_key: str, categorization_prompt: str, logger: logging.Logger
    ):
        """
        Initialize invoice renamer.

        Args:
            api_key: Anthropic API key
            categorization_prompt: Prompt for categorizing invoices
            logger: Logger instance
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.categorization_prompt = categorization_prompt
        self.logger = logger

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text content from a PDF file."""
        try:
            text = ""
            with open(pdf_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page_num in range(len(pdf_reader.pages)):
                    page = pdf_reader.pages[page_num]
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                    else:
                        text += (
                            f"[Page {page_num+1}: No text found or page is an image]\n"
                        )
            return text
        except Exception as e:
            self.logger.error(f"Error during text extraction from '{pdf_path}': {e}")
            return f"Error during text extraction: {e}"

    def convert_pdf_to_images(
        self, pdf_path: str, max_pages: int = MAX_PDF_PAGES
    ) -> List[str]:
        """Convert PDF to images and return a list of base64-encoded images."""
        try:
            images = convert_from_path(
                pdf_path, dpi=DEFAULT_DPI, first_page=1, last_page=max_pages
            )

            image_base64_list = []
            for i, image in enumerate(images):
                buffered = io.BytesIO()
                image.save(buffered, format="JPEG", quality=DEFAULT_IMAGE_QUALITY)
                img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                image_base64_list.append(img_base64)

                if i >= max_pages - 1:
                    break

            return image_base64_list
        except Exception as e:
            self.logger.error(
                f"Error during PDF to image conversion for '{pdf_path}': {e}"
            )
            return []

    def _build_categorization_prompt(self, extracted_text: str) -> str:
        """Build the categorization prompt with extracted text."""
        return f"""{self.categorization_prompt}

Extracted text from invoice (for verification only, primary extract from images):
{extracted_text}

IMPORTANT: Look closely at the invoice images to extract this information. The extracted text is only provided as a backup.
"""

    def _build_message_content(
        self, prompt: str, images_base64: List[str]
    ) -> List[Dict]:
        """Build the message content for Claude API."""
        content = [{"type": "text", "text": prompt}]

        # Add images
        for img_base64 in images_base64:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_base64,
                    },
                }
            )

        return content

    def _parse_claude_response(self, response_text: str) -> Dict[str, str]:
        """Parse Claude's response to extract invoice data."""
        json_match = re.search(r"({[\s\S]*?})", response_text)
        if not json_match:
            self.logger.error("No JSON found in Claude's response")
            return self.DEFAULT_INVOICE_DATA.copy()

        json_str = json_match.group(1)
        try:
            invoice_data = json.loads(json_str)

            # Ensure all required keys are present
            for key in self.DEFAULT_INVOICE_DATA:
                if key not in invoice_data:
                    invoice_data[key] = "Unknown"

            return invoice_data
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON from Claude's response: {e}")
            return self.DEFAULT_INVOICE_DATA.copy()

    def analyze_invoice_with_claude(
        self, pdf_path: str, extracted_text: str
    ) -> Dict[str, str]:
        """
        Use Claude API to analyze invoice content and extract key information.
        Returns a dictionary with date, company, invoice_number, and description.
        """
        try:
            self.logger.info(f"Converting PDF to images for OCR: {pdf_path}")
            image_base64_list = self.convert_pdf_to_images(pdf_path)

            if not image_base64_list:
                self.logger.warning(
                    "PDF to image conversion failed, using text-only extraction."
                )

            # Prepare the prompt
            prompt = self._build_categorization_prompt(extracted_text)

            # Prepare message content with text and images
            if image_base64_list:
                content = self._build_message_content(prompt, image_base64_list)
                self.logger.info(
                    f"Sending request to Claude with {len(image_base64_list)} images..."
                )
            else:
                content = prompt
                self.logger.info("Sending text-only request to Claude...")

            # Send request to Claude
            message = self.client.messages.create(
                model=CLAUDE_SONNET_MODEL,
                max_tokens=MAX_TOKENS_CATEGORIZATION,
                temperature=DEFAULT_TEMPERATURE,
                messages=[{"role": "user", "content": content}],
            )

            # Extract response text
            response_text = ""
            for content_block in message.content:
                if content_block.type == "text":
                    response_text += content_block.text

            # Parse the response
            return self._parse_claude_response(response_text)

        except anthropic.APIError as e:
            self.logger.error(f"Anthropic API error for '{pdf_path}': {e}")
            return self.DEFAULT_INVOICE_DATA.copy()
        except Exception as e:
            self.logger.error(
                f"Unexpected error during Claude API request for '{pdf_path}': {e}"
            )
            return self.DEFAULT_INVOICE_DATA.copy()

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize the filename by removing invalid characters."""
        invalid_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
        for char in invalid_chars:
            filename = filename.replace(char, "_")

        # Limit length (reserve space for potential counter and extension)
        max_base_length = MAX_FILENAME_LENGTH - 20
        return filename[:max_base_length]

    def generate_new_filename(
        self, invoice_data: Dict[str, str], original_extension: str
    ) -> str:
        """Generate a new filename based on the invoice data."""
        components = [
            invoice_data.get("date", "Unknown"),
            invoice_data.get("company", "Unknown"),
            invoice_data.get("invoice_number", "Unknown"),
            invoice_data.get("description", "Unknown"),
        ]

        sanitized_components = [
            self.sanitize_filename(str(comp)) for comp in components
        ]
        new_filename = ", ".join(sanitized_components) + original_extension

        return new_filename

    def _get_unique_filepath(self, base_path: str) -> str:
        """Generate a unique filepath by adding counter if file exists."""
        if not os.path.exists(base_path):
            return base_path

        # Add counter to make unique
        counter = 1
        name_parts = os.path.splitext(base_path)
        new_path = base_path

        while os.path.exists(new_path):
            new_path = f"{name_parts[0]}_{counter}{name_parts[1]}"
            counter += 1

        return new_path

    def _delete_pdf_with_companion(self, pdf_path: str) -> bool:
        """Delete a PDF and its companion ``.xml`` file (same base name) if it exists.

        Returns True on successful PDF deletion.
        """
        xml_path = os.path.splitext(pdf_path)[0] + ".xml"
        try:
            os.remove(pdf_path)
        except OSError as e:
            self.logger.error(f"Failed to delete '{pdf_path}': {e}")
            return False
        if os.path.exists(xml_path):
            try:
                os.remove(xml_path)
            except OSError as e:
                self.logger.error(f"Failed to delete companion '{xml_path}': {e}")
        return True

    def _move_companion_xml_alongside(self, source_pdf: str, dest_pdf: str) -> None:
        """If ``source_pdf`` has a sibling ``.xml``, move it next to ``dest_pdf``.

        The moved XML shares ``dest_pdf``'s base name (only the extension
        differs), and uses ``_get_unique_filepath`` to avoid overwriting.
        No-op when no companion XML exists.
        """
        old_xml = os.path.splitext(source_pdf)[0] + ".xml"
        if not os.path.exists(old_xml):
            return
        new_xml = self._get_unique_filepath(os.path.splitext(dest_pdf)[0] + ".xml")
        try:
            shutil.move(old_xml, new_xml)
            self.logger.info(f"Moved companion XML to: {os.path.basename(new_xml)}")
        except OSError as e:
            self.logger.error(f"Failed to move companion XML '{old_xml}': {e}")

    def _move_pdf_with_companion(self, pdf_path: str, output_dir: str) -> Optional[str]:
        """Move a PDF (and companion ``.xml`` if present) into ``output_dir``.

        Uses ``_get_unique_filepath`` to avoid overwriting existing files.
        Returns the new PDF path on success, None on failure.
        """
        os.makedirs(output_dir, exist_ok=True)
        target_pdf = self._get_unique_filepath(
            os.path.join(output_dir, os.path.basename(pdf_path))
        )
        old_xml = os.path.splitext(pdf_path)[0] + ".xml"

        try:
            shutil.move(pdf_path, target_pdf)
        except OSError as e:
            self.logger.error(f"Failed to move '{pdf_path}' to output/: {e}")
            return None

        if os.path.exists(old_xml):
            target_xml = os.path.splitext(target_pdf)[0] + ".xml"
            try:
                shutil.move(old_xml, target_xml)
            except OSError as e:
                self.logger.error(f"Failed to move companion XML '{old_xml}': {e}")

        return target_pdf

    def _relocate_renamed_files(self, directory: str) -> int:
        """Move already-renamed top-level PDFs (and XML companions) into output/.

        Returns the number of PDFs relocated. Un-renamed PDFs and files under
        ``output/`` are left untouched.
        """
        output_dir = os.path.join(directory, "output")
        relocated = 0
        try:
            entries = os.listdir(directory)
        except OSError as e:
            self.logger.error(f"Cannot list '{directory}': {e}")
            return 0

        for name in entries:
            if not name.lower().endswith(".pdf"):
                continue
            if not self._is_renamed_file(name):
                continue
            if self._has_generic_description(name):
                # Placeholder description like "ksef" — let Claude upgrade it.
                continue
            src = os.path.join(directory, name)
            if not os.path.isfile(src):
                continue
            moved = self._move_pdf_with_companion(src, output_dir)
            if moved:
                relocated += 1
                self.logger.info(f"Moved to output/: {os.path.basename(moved)}")

        return relocated

    @staticmethod
    def _make_dedup_key(
        company: Optional[str], invoice_number: Optional[str]
    ) -> Optional[Tuple[str, str]]:
        """
        Build a (company, invoice_number) dedup key, normalized to lowercase.

        Returns None if either value is missing or Unknown — callers should
        then fall back to checksum-based dedup only.
        """
        if not company or not invoice_number:
            return None
        if company == "Unknown" or invoice_number == "Unknown":
            return None
        return (company.strip().lower(), invoice_number.strip().lower())

    def copy_invoice_file(
        self,
        file_path: str,
        invoice_data: Dict[str, str],
        known_keys: Optional[Set[Tuple[str, str]]] = None,
        known_checksums: Optional[Set[str]] = None,
    ) -> Tuple[bool, str]:
        """
        Copy the invoice file to the output/ subdirectory with updated name.

        Skips duplicate invoices based on (company, invoice_number) or checksum.

        Returns (success, new_file_path). Returns (False, "") for duplicates.
        """
        try:
            # Check for duplicate by (company, invoice_number) pair
            key = self._make_dedup_key(
                invoice_data.get("company"),
                invoice_data.get("invoice_number"),
            )
            if known_keys is not None and key is not None and key in known_keys:
                self.logger.info(
                    f"Skipping duplicate (company '{key[0]}', invoice '{key[1]}'): "
                    f"{os.path.basename(file_path)}"
                )
                return False, ""

            # Check for duplicate by file checksum
            if known_checksums is not None:
                checksum = self._file_checksum(file_path)
                if checksum in known_checksums:
                    self.logger.info(
                        f"Skipping duplicate (identical content): "
                        f"{os.path.basename(file_path)}"
                    )
                    return False, ""

            source_dir = os.path.dirname(file_path) or "."
            output_dir = os.path.join(source_dir, "output")
            os.makedirs(output_dir, exist_ok=True)

            _, extension = os.path.splitext(file_path)
            new_filename = self.generate_new_filename(invoice_data, extension)
            new_file_path = os.path.join(output_dir, new_filename)

            # Get unique filepath
            new_file_path = self._get_unique_filepath(new_file_path)

            # Copy the file
            shutil.copy2(file_path, new_file_path)
            self.logger.info(f"File copied to: {new_file_path}")

            # Update dedup index
            if known_keys is not None and key is not None:
                known_keys.add(key)
            if known_checksums is not None:
                known_checksums.add(self._file_checksum(new_file_path))

            return True, new_file_path

        except PermissionError as e:
            self.logger.error(f"Permission denied copying file '{file_path}': {e}")
            return False, file_path
        except OSError as e:
            self.logger.error(f"OS error copying file '{file_path}': {e}")
            return False, file_path
        except Exception as e:
            self.logger.error(f"Unexpected error copying file '{file_path}': {e}")
            return False, file_path

    def _validate_pdf_file(self, file_path: str) -> bool:
        """Validate that file exists and is a PDF."""
        if not os.path.exists(file_path):
            self.logger.error(f"File '{file_path}' does not exist!")
            return False

        if not file_path.lower().endswith(".pdf"):
            self.logger.error(f"File '{file_path}' is not a PDF file!")
            return False

        return True

    def process_invoice_file(
        self,
        file_path: str,
        known_keys: Optional[Set[Tuple[str, str]]] = None,
        known_checksums: Optional[Set[str]] = None,
        consume_source: bool = False,
    ) -> bool:
        """
        Process a single invoice file: extract info, analyze, and copy with a new name.

        Skips duplicates if dedup sets are provided. When ``consume_source`` is
        True (directory mode), the source PDF is removed from disk after either
        a successful rename or a logical-duplicate detection, and any companion
        ``.xml`` next to the source is moved into ``output/`` alongside the
        newly-named PDF so the XML/PDF pair stays linked.

        Returns True if successful, False otherwise.
        """
        # Validate file
        if not self._validate_pdf_file(file_path):
            return False

        self.logger.info(f"Processing file: {os.path.basename(file_path)}")

        try:
            # Extract text from PDF
            self.logger.info("Extracting text...")
            extracted_text = self.extract_text_from_pdf(file_path)

            # Analyze with Claude API
            self.logger.info("Analyzing invoice content with Claude...")
            invoice_data = self.analyze_invoice_with_claude(file_path, extracted_text)

            # Show extracted information
            self.logger.info("Extracted information:")
            for key, value in invoice_data.items():
                self.logger.info(f"  {key}: {value}")

            # Copy the file with new name (with dedup)
            self.logger.info("Copying file with new name...")
            success, new_file_path = self.copy_invoice_file(
                file_path, invoice_data, known_keys, known_checksums
            )

            if success:
                self.logger.info(
                    f"Success! File copied to: {os.path.basename(new_file_path)}"
                )
                if consume_source:
                    self._move_companion_xml_alongside(file_path, new_file_path)
                    try:
                        os.remove(file_path)
                        self.logger.info(
                            f"Removed source: {os.path.basename(file_path)}"
                        )
                    except OSError as e:
                        self.logger.error(f"Failed to remove source '{file_path}': {e}")
            elif new_file_path == "":
                self.logger.info("Skipped (duplicate).")
                if consume_source:
                    if self._delete_pdf_with_companion(file_path):
                        self.logger.info(
                            f"Deleted source duplicate: {os.path.basename(file_path)}"
                        )
                return True  # Not an error, just a duplicate
            else:
                self.logger.error("Failed to copy file.")

            return success

        except Exception as e:
            self.logger.error(f"Unexpected error processing file '{file_path}': {e}")
            return False

    @staticmethod
    def _file_checksum(file_path: str) -> str:
        """Compute SHA256 checksum of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _extract_key_from_filename(filename: str) -> Optional[Tuple[str, str]]:
        """
        Extract the (company, invoice_number) dedup key from a renamed filename.

        Expected format: YYYY-MM-DD, Company, InvoiceNumber, Description.pdf
        Returns None if the filename doesn't contain both fields.
        """
        parts = filename.rsplit(".", 1)[0].split(", ")
        if len(parts) >= 3:
            company = parts[1].strip().lower()
            invoice_number = parts[2].strip().lower()
            if company and invoice_number:
                return (company, invoice_number)
        return None

    @staticmethod
    def _is_renamed_file(filename: str) -> bool:
        """Check if filename matches the renamed convention (YYYY-MM-DD, ...)."""
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}, ", filename))

    # Descriptions written by non-Claude sources that should be upgraded to a
    # real human-readable description on the next rename pass.
    _GENERIC_DESCRIPTIONS = {"ksef"}

    @staticmethod
    def _extract_description_from_filename(filename: str) -> Optional[str]:
        """Extract the lowercase description segment from a renamed filename."""
        parts = filename.rsplit(".", 1)[0].split(", ")
        if len(parts) >= 4:
            return parts[3].strip().lower()
        return None

    def _has_generic_description(self, filename: str) -> bool:
        """True if the filename's description segment is a placeholder like 'ksef'."""
        desc = self._extract_description_from_filename(filename)
        return desc in self._GENERIC_DESCRIPTIONS

    def _build_dedup_index(
        self, directory: str
    ) -> Tuple[Set[Tuple[str, str]], Set[str]]:
        """
        Build dedup index from files previously written to ``<directory>/output/``.

        Scoped to the ``output/`` subdirectory only (recursive within it).
        Files at the top level of ``directory`` are excluded because they are
        the source files about to be processed — including them here causes a
        self-match where every file is flagged as identical to itself.

        Args:
            directory: Source directory being processed.

        Returns:
            Tuple of (known_keys, checksums) where keys are lowercase
            (company, invoice_number) tuples.
        """
        known_keys: Set[Tuple[str, str]] = set()
        known_checksums: Set[str] = set()

        output_dir = os.path.join(directory, "output")
        if not os.path.isdir(output_dir):
            return known_keys, known_checksums

        for root, _, files in os.walk(output_dir):
            for f in files:
                if not self._is_renamed_file(f):
                    continue

                full_path = os.path.join(root, f)

                # Extract (company, invoice_number) key from filename
                key = self._extract_key_from_filename(f)
                if key:
                    known_keys.add(key)

                # Compute checksum
                try:
                    known_checksums.add(self._file_checksum(full_path))
                except OSError:
                    pass

        return known_keys, known_checksums

    def _find_pdf_files(self, directory: str) -> List[str]:
        """Find PDF files that need Claude analysis.

        Includes both fully un-renamed PDFs and renamed-but-generic PDFs
        (e.g. KSeF saves whose 4th segment is the placeholder ``ksef``).
        Already-renamed PDFs with a real description are skipped.
        """
        try:
            pdf_files: List[str] = []
            for f in os.listdir(directory):
                if not f.lower().endswith(".pdf"):
                    continue
                if self._is_renamed_file(f) and not self._has_generic_description(f):
                    continue
                pdf_files.append(os.path.join(directory, f))
            return pdf_files
        except PermissionError as e:
            self.logger.error(
                f"Permission denied accessing directory '{directory}': {e}"
            )
            return []
        except Exception as e:
            self.logger.error(f"Error listing files in directory '{directory}': {e}")
            return []

    def _list_all_source_pdfs(self, directory: str) -> List[str]:
        """List every PDF directly in `directory` (non-recursive).

        Does not filter by the renamed-file convention — this method backs the
        source-dedup pass, which must see already-renamed files too (e.g. KSeF
        saves like `ksef.pdf`, `ksef_1.pdf` all match the convention but are
        still logical duplicates that need collapsing).

        The `output/` subdirectory is naturally skipped because `os.listdir`
        returns it as a directory entry that fails the `.pdf` extension check.
        """
        try:
            return [
                os.path.join(directory, f)
                for f in os.listdir(directory)
                if f.lower().endswith(".pdf")
            ]
        except PermissionError as e:
            self.logger.error(
                f"Permission denied accessing directory '{directory}': {e}"
            )
            return []
        except Exception as e:
            self.logger.error(f"Error listing files in directory '{directory}': {e}")
            return []

    def _remove_source_duplicates(self, directory: str) -> int:
        """
        Delete duplicate PDFs in the source directory.

        Runs two dedup passes over every PDF in `directory` (including already-
        renamed ones). The ``output/`` subdirectory is excluded because only
        top-level files are listed.

        Pass 1 — byte-identical: groups by SHA256, keeps the alphabetically-
        first file per group, deletes the rest. Also deletes any source PDF
        whose hash matches a file already present in ``output/``.

        Pass 2 — logical duplicate: for files matching the renamed convention
        ``YYYY-MM-DD, Company, InvoiceNumber, ...``, groups by lowercase
        ``(company, invoice_number)`` extracted from the filename. Keeps the
        alphabetically-first file, deletes the rest. This catches duplicates
        whose PDF bytes differ (e.g. KSeF `_1`/`_2` saves produced by
        non-deterministic rendering) but which represent the same invoice.

        Args:
            directory: Source directory to scan (non-recursive).

        Returns:
            Number of files deleted.
        """
        all_pdfs = self._list_all_source_pdfs(directory)
        if not all_pdfs:
            return 0

        # Collect checksums of already-processed files in output/
        output_dir = os.path.join(directory, "output")
        processed_checksums: Set[str] = set()
        if os.path.isdir(output_dir):
            for name in os.listdir(output_dir):
                if not name.lower().endswith(".pdf"):
                    continue
                full = os.path.join(output_dir, name)
                if not os.path.isfile(full):
                    continue
                try:
                    processed_checksums.add(self._file_checksum(full))
                except OSError as e:
                    self.logger.warning(f"Cannot checksum '{full}': {e}")

        deleted = 0

        # Pass 1: group by SHA256.
        by_checksum: Dict[str, List[str]] = {}
        for path in all_pdfs:
            try:
                cs = self._file_checksum(path)
            except OSError as e:
                self.logger.warning(f"Cannot checksum '{path}': {e}")
                continue
            by_checksum.setdefault(cs, []).append(path)

        for cs, paths in by_checksum.items():
            paths.sort()
            if cs in processed_checksums:
                for p in paths:
                    if self._delete_pdf_with_companion(p):
                        self.logger.info(
                            f"Deleted (already in output/): {os.path.basename(p)}"
                        )
                        deleted += 1
            elif len(paths) > 1:
                keeper = paths[0]
                self.logger.info(
                    f"Keeping '{os.path.basename(keeper)}', removing "
                    f"{len(paths) - 1} byte-identical duplicate(s)"
                )
                for p in paths[1:]:
                    if self._delete_pdf_with_companion(p):
                        self.logger.info(f"  deleted: {os.path.basename(p)}")
                        deleted += 1

        # Pass 2: group renamed files by (company, invoice_number) from filename.
        remaining = [p for p in all_pdfs if os.path.exists(p)]
        by_logical: Dict[Tuple[str, str], List[str]] = {}
        for path in remaining:
            key = self._extract_key_from_filename(os.path.basename(path))
            if key is None:
                continue
            by_logical.setdefault(key, []).append(path)

        for key, paths in by_logical.items():
            if len(paths) <= 1:
                continue
            paths.sort()
            keeper = paths[0]
            self.logger.info(
                f"Keeping '{os.path.basename(keeper)}', removing "
                f"{len(paths) - 1} logical duplicate(s) for "
                f"(company='{key[0]}', invoice='{key[1]}')"
            )
            for p in paths[1:]:
                if self._delete_pdf_with_companion(p):
                    self.logger.info(f"  deleted: {os.path.basename(p)}")
                    deleted += 1

        return deleted

    def process_directory(self, directory: str) -> Tuple[int, int]:
        """
        Process all PDF files in a directory.

        Args:
            directory: Directory containing PDF files

        Returns:
            Tuple of (processed_count, error_count)
        """
        # Validate directory
        if not os.path.exists(directory):
            self.logger.error(f"Directory '{directory}' does not exist!")
            return 0, 0

        if not os.path.isdir(directory):
            self.logger.error(f"'{directory}' is not a directory!")
            return 0, 0

        # Dedup first — covers both un-renamed and already-renamed files.
        # Must run before the pdf_files check so KSeF saves like
        # `ksef.pdf` / `ksef_1.pdf` / `ksef_2.pdf` (all matching the renamed
        # convention, so invisible to `_find_pdf_files`) still get collapsed.
        # Companion `.xml` files are deleted alongside their PDF siblings.
        self.logger.info("Checking source directory for duplicate PDFs...")
        deleted = self._remove_source_duplicates(directory)
        if deleted:
            self.logger.info(f"Removed {deleted} duplicate PDF(s) from source.")
        else:
            self.logger.info("No duplicates found in source.")

        # Relocate every already-renamed top-level PDF (and companion XML) into
        # output/. They don't need Claude analysis — their names are already in
        # canonical form.
        self.logger.info("Moving already-renamed files to output/...")
        relocated = self._relocate_renamed_files(directory)
        if relocated:
            self.logger.info(f"Moved {relocated} already-renamed file(s) to output/.")

        # Find un-renamed PDFs for Claude-based renaming.
        pdf_files = self._find_pdf_files(directory)

        if not pdf_files:
            self.logger.info(f"No un-renamed PDFs to process in '{directory}'.")
            return 0, 0

        self.logger.info(
            f"Found {len(pdf_files)} PDF file(s) needing rename in '{directory}'"
        )

        # Build dedup index from existing renamed files
        self.logger.info("Building dedup index from existing files...")
        known_keys, known_checksums = self._build_dedup_index(directory)
        self.logger.info(
            f"Dedup index: {len(known_keys)} (company, invoice) pairs, "
            f"{len(known_checksums)} file checksums"
        )

        processed_count = 0
        error_count = 0

        for pdf_path in pdf_files:
            try:
                if self.process_invoice_file(
                    pdf_path,
                    known_keys,
                    known_checksums,
                    consume_source=True,
                ):
                    processed_count += 1
                else:
                    error_count += 1
            except Exception as e:
                self.logger.error(f"Error processing file '{pdf_path}': {e}")
                error_count += 1

        return processed_count, error_count
