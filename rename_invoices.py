#!/usr/bin/env python3
import argparse
import sys
import os
import PyPDF2
import anthropic
import re
import base64
import io
from pdf2image import convert_from_path
from PIL import Image
from typing import List, Dict, Optional, Tuple


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments for invoice renaming."""
    parser = argparse.ArgumentParser(description="Rename invoice PDF files based on their content.")
    parser.add_argument(
        "--files", nargs="+", required=True, help="List of PDF invoice files to process"
    )
    parser.add_argument(
        "--api-key", "-k", required=True, help="Claude API key for processing invoices"
    )

    return parser.parse_args()


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text content from a PDF file. Fallback method."""
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
                    text += f"[Page {page_num+1}: No text found or page is an image]\n"
        return text
    except Exception as e:
        print(f"Error during text extraction from '{pdf_path}': {str(e)}")
        return f"Error during text extraction: {str(e)}"


def convert_pdf_to_images(pdf_path: str, max_pages: int = 10) -> List[str]:
    """
    Convert PDF to images and return a list of base64-encoded images.
    """
    try:
        # Convert PDF to images
        images = convert_from_path(pdf_path, dpi=150, first_page=1, last_page=max_pages)

        # Convert images to base64 without saving to disk
        image_base64_list = []
        for i, image in enumerate(images):
            # Compress and convert to base64 for Claude
            buffered = io.BytesIO()
            image.save(buffered, format="JPEG", quality=85)
            img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
            image_base64_list.append(img_base64)

            # Limit number of pages
            if i >= max_pages - 1:
                break

        return image_base64_list
    except Exception as e:
        print(f"Error during PDF to image conversion for '{pdf_path}': {str(e)}")
        return []


def analyze_invoice_with_claude(pdf_path: str, extracted_text: str, api_key: str) -> Dict[str, str]:
    """
    Use Claude API to analyze invoice content and extract key information.
    Returns a dictionary with date, company, invoice_number, and description.
    Uses both extracted text and PDF screenshots.
    """
    try:
        # Initialize Anthropic client
        client = anthropic.Anthropic(api_key=api_key)

        # Convert PDF to images
        print("Converting PDF to images for OCR...")
        image_base64_list = convert_pdf_to_images(pdf_path)

        if not image_base64_list:
            print("Warning: PDF to image conversion failed, using text-only extraction.")

        # Prepare the prompt for Claude
        prompt = f"""Extract the following information from this invoice:

Extracted text from invoice (for verification only, primary extract from images):
{extracted_text}

Please extract:
1. Invoice date (in format YYYY-MM-DD)
2. Company name
3. Invoice number
4. Brief description of what the invoice is for

For the description, be concise and use categories like these examples:
- abonament na oprogramowanie
- akcesoria do druku 3d
- domena internetowa
- doradztwo IT
- filtry do wody
- herbata do biura
- karta pamięci
- licencja na oprogramowanie
- materiały do druku
- narzędzia
- olej silnikowy
- paliwo
- remont biura
- serwis samochodu
- słuchawki
- środki czystości
- usługi księgowe
- usługi telekomunikacyjne
- zegarek elektroniczny

Respond in this exact JSON format:
{{
  "date": "YYYY-MM-DD",
  "company": "Company Name",
  "invoice_number": "INV12345",
  "description": "Brief description"
}}

If you can't find some information, use "Unknown" as the value.

IMPORTANT: Look closely at the invoice images to extract this information. The extracted text is only provided as a backup.
"""

        # Prepare message content with text and images
        content = [{"type": "text", "text": prompt}]

        # Add images as attachments in the order of PDF pages
        for i, img_base64 in enumerate(image_base64_list):
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

        print(f"Sending request to Claude with {len(image_base64_list)} images...")

        # Send request to Claude
        message = client.messages.create(
            model="claude-3-7-sonnet-20250219",
            max_tokens=1000,
            temperature=0.0,  # Use 0 for more deterministic responses
            messages=[{"role": "user", "content": content if image_base64_list else prompt}],
        )

        # Extract response text
        response_text = ""
        for content_block in message.content:
            if content_block.type == "text":
                response_text += content_block.text

        # Parse the JSON response
        import json

        # Find JSON in response (looking for content between curly braces)
        json_match = re.search(r"({[\s\S]*?})", response_text)
        if json_match:
            json_str = json_match.group(1)
            try:
                invoice_data = json.loads(json_str)
                # Verify the expected keys exist
                required_keys = ["date", "company", "invoice_number", "description"]
                for key in required_keys:
                    if key not in invoice_data:
                        invoice_data[key] = "Unknown"
                return invoice_data
            except json.JSONDecodeError:
                print(f"Failed to parse JSON from Claude's response for '{pdf_path}'")
                return {
                    "date": "Unknown",
                    "company": "Unknown",
                    "invoice_number": "Unknown",
                    "description": "Unknown",
                }
        else:
            print(f"No JSON found in Claude's response for '{pdf_path}'")
            return {
                "date": "Unknown",
                "company": "Unknown",
                "invoice_number": "Unknown",
                "description": "Unknown",
            }

    except Exception as e:
        print(f"Error during Claude API request for '{pdf_path}': {str(e)}")
        return {
            "date": "Unknown",
            "company": "Unknown",
            "invoice_number": "Unknown",
            "description": "Unknown",
        }


def sanitize_filename(filename: str) -> str:
    """Sanitize the filename by removing invalid characters."""
    # Replace characters that are invalid in filenames
    invalid_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
    for char in invalid_chars:
        filename = filename.replace(char, "_")

    # Limit length of each component and total filename
    return filename[:240]  # Leave some room for the extension


def generate_new_filename(invoice_data: Dict[str, str], original_extension: str) -> str:
    """Generate a new filename based on the invoice data."""
    # Format: date, company, invoice_number, description.extension
    components = [
        invoice_data.get("date", "Unknown"),
        invoice_data.get("company", "Unknown"),
        invoice_data.get("invoice_number", "Unknown"),
        invoice_data.get("description", "Unknown"),
    ]

    # Sanitize each component
    sanitized_components = [sanitize_filename(str(comp)) for comp in components]

    # Join with commas and add original extension
    new_filename = ", ".join(sanitized_components) + original_extension

    return new_filename


def copy_invoice_file(file_path: str, invoice_data: Dict[str, str]) -> Tuple[bool, str]:
    """
    Copy the invoice file to a new file with updated name based on extracted information.
    Returns (success, new_file_path).
    """
    try:
        import shutil

        # Get directory and original extension
        directory = os.path.dirname(file_path)
        _, extension = os.path.splitext(file_path)

        # Generate new filename
        new_filename = generate_new_filename(invoice_data, extension)

        # Full path for the new file
        if directory:
            new_file_path = os.path.join(directory, new_filename)
        else:
            new_file_path = new_filename

        # Check if new filename already exists
        counter = 1
        base_new_path = new_file_path
        while os.path.exists(new_file_path):
            # Add a counter to make the filename unique
            name_parts = os.path.splitext(base_new_path)
            new_file_path = f"{name_parts[0]}_{counter}{name_parts[1]}"
            counter += 1

        # Copy the file instead of renaming
        shutil.copy2(file_path, new_file_path)

        return True, new_file_path

    except Exception as e:
        print(f"Error copying file '{file_path}': {str(e)}")
        return False, file_path


def process_invoice_file(file_path: str, api_key: str) -> None:
    """Process a single invoice file: extract info, analyze, and copy with a new name."""
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' does not exist!")
        return

    if not file_path.lower().endswith(".pdf"):
        print(f"Error: File '{file_path}' is not a PDF file!")
        return

    print(f"\nProcessing file: {os.path.basename(file_path)}")

    # Extract text from PDF (as backup and for context)
    print("Extracting text...")
    extracted_text = extract_text_from_pdf(file_path)

    # Analyze with Claude API (using both text and screenshots)
    print("Analyzing invoice content with Claude...")
    invoice_data = analyze_invoice_with_claude(file_path, extracted_text, api_key)

    # Show extracted information
    print("\nExtracted information:")
    for key, value in invoice_data.items():
        print(f"- {key}: {value}")

    # Copy the file with new name
    print("\nCopying file with new name...")
    success, new_file_path = copy_invoice_file(file_path, invoice_data)

    if success:
        print(f"Success! File copied to: {os.path.basename(new_file_path)}")
    else:
        print(f"Failed to copy file.")


def main() -> None:
    """Main function to process and copy invoice files with updated names."""
    # Parse command line arguments
    args = parse_arguments()

    # Validate API key
    api_key = args.api_key
    if not api_key:
        print("Error: No API key provided.")
        sys.exit(1)

    print(f"Using API key: {api_key[:5]}{'*' * 10}")

    # Process each file
    processed_count = 0
    error_count = 0

    for file_path in args.files:
        try:
            process_invoice_file(file_path, api_key)
            processed_count += 1
        except Exception as e:
            print(f"Error processing file '{file_path}': {str(e)}")
            error_count += 1

    # Summary
    print(f"\nSummary: Processed {processed_count} files with {error_count} errors.")


if __name__ == "__main__":
    main()
