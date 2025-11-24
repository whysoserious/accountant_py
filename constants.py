#!/usr/bin/env python3
"""Constants used throughout the accountant application."""

# File types
VALID_FILE_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp"]
VALID_CONTENT_TYPES = [
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/bmp",
]

# Gmail specific
GMAIL_FOLDERS = [
    'INBOX',
    '"[Gmail]/All Mail"',
    '[Gmail]/All Mail',
    '"[Gmail]/Wszystkie"',  # Polish
    '[Gmail]/Wszystkie',
    'All Mail'
]

# Claude API
CLAUDE_SONNET_MODEL = "claude-3-7-sonnet-20250219"
CLAUDE_HAIKU_MODEL = "claude-3-haiku-20240307"
MAX_TOKENS_NIP_CHECK = 100
MAX_TOKENS_NIP_EXTRACT = 50
MAX_TOKENS_CATEGORIZATION = 400

# Image processing
DEFAULT_DPI = 150
DEFAULT_IMAGE_QUALITY = 85
MAX_PDF_PAGES = 5
MAX_FILENAME_LENGTH = 200

# NIP validation
NIP_LENGTH = 10

# Default values
DEFAULT_TEMPERATURE = 0.0