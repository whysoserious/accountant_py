#!/usr/bin/env python3
"""Invoice renaming functionality."""
import os
import shutil
import re
import json
import base64
import io
import logging
from typing import Dict, List, Tuple, Optional
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
    MAX_FILENAME_LENGTH
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

    def _build_message_content(self, prompt: str, images_base64: List[str]) -> List[Dict]:
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
                messages=[
                    {"role": "user", "content": content}
                ],
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
            self.logger.error(f"Unexpected error during Claude API request for '{pdf_path}': {e}")
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

        sanitized_components = [self.sanitize_filename(str(comp)) for comp in components]
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

    def copy_invoice_file(
        self, file_path: str, invoice_data: Dict[str, str]
    ) -> Tuple[bool, str]:
        """
        Copy the invoice file to a new file with updated name.
        Returns (success, new_file_path).
        """
        try:
            directory = os.path.dirname(file_path)
            _, extension = os.path.splitext(file_path)

            new_filename = self.generate_new_filename(invoice_data, extension)

            if directory:
                new_file_path = os.path.join(directory, new_filename)
            else:
                new_file_path = new_filename

            # Get unique filepath
            new_file_path = self._get_unique_filepath(new_file_path)

            # Copy the file
            shutil.copy2(file_path, new_file_path)
            self.logger.info(f"File copied to: {new_file_path}")

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

    def process_invoice_file(self, file_path: str) -> bool:
        """
        Process a single invoice file: extract info, analyze, and copy with a new name.
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

            # Copy the file with new name
            self.logger.info("Copying file with new name...")
            success, new_file_path = self.copy_invoice_file(file_path, invoice_data)

            if success:
                self.logger.info(f"Success! File copied to: {os.path.basename(new_file_path)}")
            else:
                self.logger.error("Failed to copy file.")

            return success

        except Exception as e:
            self.logger.error(f"Unexpected error processing file '{file_path}': {e}")
            return False

    def _find_pdf_files(self, directory: str) -> List[str]:
        """Find all PDF files in a directory."""
        try:
            pdf_files = [
                os.path.join(directory, f)
                for f in os.listdir(directory)
                if f.lower().endswith(".pdf")
            ]
            return pdf_files
        except PermissionError as e:
            self.logger.error(f"Permission denied accessing directory '{directory}': {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error listing files in directory '{directory}': {e}")
            return []

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

        # Find all PDF files
        pdf_files = self._find_pdf_files(directory)

        if not pdf_files:
            self.logger.warning(f"No PDF files found in '{directory}'")
            return 0, 0

        self.logger.info(f"Found {len(pdf_files)} PDF files in '{directory}'")

        processed_count = 0
        error_count = 0

        for pdf_path in pdf_files:
            try:
                if self.process_invoice_file(pdf_path):
                    processed_count += 1
                else:
                    error_count += 1
            except Exception as e:
                self.logger.error(f"Error processing file '{pdf_path}': {e}")
                error_count += 1

        return processed_count, error_count