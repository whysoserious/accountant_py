#!/usr/bin/env python3
"""Attachment processor for checking NIP and saving files."""

import os
import base64
import io
import logging
from typing import Optional, Tuple, List
from dataclasses import dataclass
from PIL import Image
import anthropic
from pdf2image import convert_from_bytes
from imap_client import EmailAttachment
from constants import (
    CLAUDE_SONNET_MODEL,
    CLAUDE_HAIKU_MODEL,
    MAX_TOKENS_NIP_CHECK,
    MAX_TOKENS_NIP_EXTRACT,
    DEFAULT_TEMPERATURE,
    DEFAULT_DPI,
    DEFAULT_IMAGE_QUALITY,
    MAX_PDF_PAGES,
    MAX_FILENAME_LENGTH,
    NIP_LENGTH,
)


@dataclass
class ProcessResult:
    """Result of processing an attachment."""

    attachment: EmailAttachment
    contains_nip: bool
    saved_path: Optional[str]
    error: Optional[str] = None


class AttachmentProcessor:
    """Processor for checking attachments and saving them."""

    def __init__(
        self,
        api_key: str,
        nip: str,
        nip_check_prompt: str,
        logger: logging.Logger,
        blacklist_keywords: Optional[List[str]] = None,
    ):
        """
        Initialize attachment processor.

        Args:
            api_key: Anthropic API key
            nip: NIP to search for in attachments
            nip_check_prompt: Prompt template for NIP checking
            logger: Logger instance
            blacklist_keywords: List of keywords to filter out attachments
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.nip = nip
        self.nip_check_prompt = nip_check_prompt
        self.logger = logger
        self.blacklist_keywords = [kw.lower() for kw in (blacklist_keywords or [])]

    def _is_blacklisted(self, attachment: EmailAttachment) -> bool:
        """
        Check if attachment content matches any blacklist keyword.

        Extracts text from PDF and checks for keyword matches.

        Args:
            attachment: Email attachment to check

        Returns:
            True if attachment should be discarded, False otherwise
        """
        if not self.blacklist_keywords:
            return False

        try:
            if not (
                attachment.content_type == "application/pdf"
                or attachment.filename.lower().endswith(".pdf")
            ):
                return False

            import PyPDF2

            reader = PyPDF2.PdfReader(io.BytesIO(attachment.content))
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"

            text_lower = text.lower()
            for keyword in self.blacklist_keywords:
                if keyword in text_lower:
                    self.logger.info(
                        f"Blacklisted: '{attachment.filename}' " f"matches keyword '{keyword}'"
                    )
                    return True

        except Exception as e:
            self.logger.warning(f"Could not check blacklist for {attachment.filename}: {e}")

        return False

    def _check_user_nip(self, images_base64: list) -> bool:
        """
        Check if the user's NIP is in the document.

        Args:
            images_base64: List of base64-encoded images

        Returns:
            True if user's NIP is found, False otherwise
        """
        prompt = self.nip_check_prompt.format(nip=self.nip)

        # Prepare message content with images
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

        # Send request to Claude
        message = self.client.messages.create(
            model=CLAUDE_SONNET_MODEL,
            max_tokens=MAX_TOKENS_NIP_CHECK,
            temperature=DEFAULT_TEMPERATURE,
            messages=[{"role": "user", "content": content}],
        )

        # Extract response
        response_text = ""
        for content_block in message.content:
            if content_block.type == "text":
                response_text += content_block.text

        # Check if response contains YES
        return "YES" in response_text.strip().upper()

    def _extract_any_nip(self, images_base64: list) -> Optional[str]:
        """
        Extract any NIP from the document.

        Args:
            images_base64: List of base64-encoded images

        Returns:
            The extracted NIP or None if no valid NIP found
        """
        extract_prompt = (
            "Extract any Polish NIP (10-digit tax ID) from this document. "
            "Reply with ONLY the 10-digit number without dashes or spaces. "
            "If no NIP found, reply 'NONE'."
        )

        extract_content = [{"type": "text", "text": extract_prompt}]

        # Check first 3 pages
        for img_base64 in images_base64[:3]:
            extract_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_base64,
                    },
                }
            )

        extract_message = self.client.messages.create(
            model=CLAUDE_HAIKU_MODEL,
            max_tokens=MAX_TOKENS_NIP_EXTRACT,
            temperature=DEFAULT_TEMPERATURE,
            messages=[{"role": "user", "content": extract_content}],
        )

        extracted_nip = ""
        for content_block in extract_message.content:
            if content_block.type == "text":
                extracted_nip += content_block.text

        extracted_nip = extracted_nip.strip()

        # Validate NIP format
        if extracted_nip and extracted_nip != "NONE":
            clean_nip = extracted_nip.replace("-", "").replace(" ", "")
            if len(clean_nip) == NIP_LENGTH and clean_nip.isdigit():
                return clean_nip

        return None

    def check_nip(self, attachment: EmailAttachment) -> Tuple[bool, Optional[str]]:
        """
        Check if attachment contains the configured NIP or any other NIP.

        Args:
            attachment: Email attachment to check

        Returns:
            Tuple of (contains_my_nip, found_nip)
            - contains_my_nip: True if user's NIP is found
            - found_nip: The NIP found in document (user's or other), None if no NIP
        """
        try:
            self.logger.info(f"🔍 Checking NIP in attachment: {attachment.filename}")

            # Convert attachment to images for analysis
            images_base64 = self._convert_to_images(attachment)

            if not images_base64:
                self.logger.warning(
                    f"Failed to convert attachment to images: {attachment.filename}"
                )
                return False, None

            # First check for user's NIP
            contains_my_nip = self._check_user_nip(images_base64)

            if contains_my_nip:
                self.logger.info(
                    f"✅ NIP check result for {attachment.filename}: Found YOUR NIP! 🎉"
                )
                return True, self.nip

            # Try to extract ANY NIP from the document
            extracted_nip = self._extract_any_nip(images_base64)

            if extracted_nip:
                self.logger.info(f"📋 Found other NIP in {attachment.filename}: {extracted_nip}")
                return False, extracted_nip
            else:
                self.logger.info(f"❌ No NIP found in {attachment.filename}")
                return False, None

        except Exception as e:
            self.logger.error(f"Error checking NIP in {attachment.filename}: {e}")
            return False, None

    def _convert_pdf_to_images(self, content: bytes, max_pages: int) -> list:
        """
        Convert PDF content to base64-encoded images.

        Args:
            content: PDF file content
            max_pages: Maximum number of pages to convert

        Returns:
            List of base64-encoded JPEG images
        """
        images_base64 = []

        # Convert PDF to images
        images = convert_from_bytes(content, dpi=DEFAULT_DPI, first_page=1, last_page=max_pages)

        for i, image in enumerate(images):
            if i >= max_pages:
                break

            buffered = io.BytesIO()
            image.save(buffered, format="JPEG", quality=DEFAULT_IMAGE_QUALITY)
            img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
            images_base64.append(img_base64)

        return images_base64

    def _convert_image_to_base64(self, content: bytes) -> list:
        """
        Convert image content to base64-encoded JPEG.

        Args:
            content: Image file content

        Returns:
            List containing single base64-encoded JPEG image
        """
        image = Image.open(io.BytesIO(content))

        # Convert to JPEG if needed
        if image.mode in ("RGBA", "LA", "P"):
            # Convert to RGB for JPEG
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            if image.mode == "P":
                image = image.convert("RGBA")
            rgb_image.paste(image, mask=image.split()[-1] if image.mode in ("RGBA", "LA") else None)
            image = rgb_image

        buffered = io.BytesIO()
        image.save(buffered, format="JPEG", quality=DEFAULT_IMAGE_QUALITY)
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        return [img_base64]

    def _convert_to_images(
        self, attachment: EmailAttachment, max_pages: int = MAX_PDF_PAGES
    ) -> list:
        """
        Convert attachment to base64-encoded images.

        Args:
            attachment: Email attachment to convert
            max_pages: Maximum number of pages to convert (for PDFs)

        Returns:
            List of base64-encoded JPEG images
        """
        try:
            # Check if it's a PDF
            is_pdf = (
                attachment.content_type == "application/pdf"
                or attachment.filename.lower().endswith(".pdf")
            )

            if is_pdf:
                return self._convert_pdf_to_images(attachment.content, max_pages)
            else:
                return self._convert_image_to_base64(attachment.content)

        except Exception as e:
            self.logger.error(f"Error converting {attachment.filename} to images: {e}")
            return []

    def _determine_target_directory(
        self,
        output_directory: str,
        contains_my_nip: bool,
        found_nip: Optional[str],
        uncertain_directory: str,
        email_date,
    ) -> str:
        """
        Determine the target directory for saving an attachment.

        Args:
            output_directory: Main output directory
            contains_my_nip: Whether attachment contains user's NIP
            found_nip: NIP found in document
            uncertain_directory: Directory for uncertain attachments
            email_date: Date from the email

        Returns:
            Path to target directory
        """
        if contains_my_nip:
            # For user's NIP, organize by year-month
            year_month = email_date.strftime("%Y-%m")
            target_dir = os.path.join(output_directory, year_month)
            self.logger.info(f"📁 Organizing YOUR invoice into month folder: {year_month}")
        elif found_nip:
            # For other NIPs (user is seller), organize by NIP
            target_dir = os.path.join(output_directory, found_nip)
            self.logger.info(f"🏢 Organizing invoice for client NIP: {found_nip}")
        else:
            target_dir = uncertain_directory

        return target_dir

    def _generate_unique_filename(self, target_dir: str, filename: str) -> str:
        """
        Generate a unique filename by adding a counter if needed.

        Args:
            target_dir: Target directory
            filename: Original filename

        Returns:
            Unique file path
        """
        file_path = os.path.join(target_dir, filename)

        if not os.path.exists(file_path):
            return file_path

        # Add counter if file exists
        base_path = file_path
        name_parts = os.path.splitext(base_path)
        counter = 1

        while os.path.exists(file_path):
            file_path = f"{name_parts[0]}_{counter}{name_parts[1]}"
            counter += 1

        return file_path

    def save_attachment(
        self,
        attachment: EmailAttachment,
        output_directory: str,
        contains_my_nip: bool,
        found_nip: Optional[str],
        uncertain_directory: str,
    ) -> Optional[str]:
        """
        Save attachment to appropriate directory.

        Args:
            attachment: Email attachment to save
            output_directory: Main output directory
            contains_my_nip: Whether attachment contains user's NIP
            found_nip: NIP found in document (user's or other)
            uncertain_directory: Directory for attachments without confirmed NIP

        Returns:
            Path where file was saved, or None if save failed
        """
        try:
            # Determine target directory
            target_dir = self._determine_target_directory(
                output_directory,
                contains_my_nip,
                found_nip,
                uncertain_directory,
                attachment.email_date,
            )

            # Create directory if it doesn't exist
            os.makedirs(target_dir, exist_ok=True)

            # Generate filename
            filename = self._sanitize_filename(attachment.filename)

            # Get unique file path
            file_path = self._generate_unique_filename(target_dir, filename)

            # Write file
            with open(file_path, "wb") as f:
                f.write(attachment.content)

            self.logger.info(f"💾 Saved attachment to: {file_path}")
            return file_path

        except Exception as e:
            self.logger.error(f"Error saving attachment {attachment.filename}: {e}")
            return None

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename by removing invalid characters.

        Args:
            filename: Original filename

        Returns:
            Sanitized filename
        """
        # Replace invalid characters
        invalid_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
        for char in invalid_chars:
            filename = filename.replace(char, "_")

        # Limit length
        if len(filename) > MAX_FILENAME_LENGTH:
            name, ext = os.path.splitext(filename)
            filename = name[: MAX_FILENAME_LENGTH - len(ext)] + ext

        return filename

    def process_attachment(
        self,
        attachment: EmailAttachment,
        output_directory: str,
        uncertain_directory: str,
    ) -> ProcessResult:
        """
        Process an attachment: check for NIP and save to appropriate directory.

        Args:
            attachment: Email attachment to process
            output_directory: Main output directory
            uncertain_directory: Directory for uncertain attachments

        Returns:
            ProcessResult with processing outcome
        """
        try:
            # Check blacklist before sending to LLM
            if self._is_blacklisted(attachment):
                return ProcessResult(
                    attachment=attachment,
                    contains_nip=False,
                    saved_path=None,
                    error="Discarded: matched blacklist keyword",
                )

            # Check for NIP (returns tuple: contains_my_nip, found_nip)
            contains_my_nip, found_nip = self.check_nip(attachment)

            # Save attachment
            saved_path = self.save_attachment(
                attachment,
                output_directory,
                contains_my_nip,
                found_nip,
                uncertain_directory,
            )

            return ProcessResult(
                attachment=attachment,
                contains_nip=contains_my_nip,  # This tracks if it has user's NIP
                saved_path=saved_path,
                error=None if saved_path else "Failed to save file",
            )

        except Exception as e:
            self.logger.error(f"Error processing attachment {attachment.filename}: {e}")
            return ProcessResult(
                attachment=attachment,
                contains_nip=False,
                saved_path=None,
                error=str(e),
            )
