#!/usr/bin/env python3
"""QNAP NAS client for uploading invoices via File Station API."""

import os
import base64
import logging
import re
from dataclasses import dataclass
from typing import List, Optional
import requests
import urllib3

# Suppress SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class QNAPConfig:
    """Configuration for QNAP NAS connection."""

    host: str
    port: int
    username: str
    password: str
    upload_path: str
    use_ssl: bool = True
    verify_ssl: bool = False
    organize_by_month: bool = True


@dataclass
class UploadResult:
    """Result of a single file upload."""

    file_path: str
    remote_path: str
    success: bool
    error: Optional[str] = None


class QNAPClient:
    """Client for interacting with QNAP File Station API."""

    def __init__(self, config: QNAPConfig, logger: logging.Logger):
        """
        Initialize QNAP client.

        Args:
            config: QNAP connection configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        self.sid: Optional[str] = None
        protocol = "https" if config.use_ssl else "http"
        self.base_url = f"{protocol}://{config.host}:{config.port}"

    def _auth_url(self) -> str:
        """Return the authentication endpoint URL."""
        return f"{self.base_url}/cgi-bin/authLogin.cgi"

    def _filemanager_url(self) -> str:
        """Return the File Station endpoint URL."""
        return f"{self.base_url}/cgi-bin/filemanager/utilRequest.cgi"

    def login(self) -> None:
        """
        Authenticate with the QNAP NAS.

        Raises:
            ConnectionError: If connection to QNAP fails
            PermissionError: If authentication fails
        """
        self.logger.info(f"Connecting to QNAP at {self.base_url}...")

        pwd_b64 = base64.b64encode(self.config.password.encode()).decode()

        try:
            response = requests.post(
                self._auth_url(),
                data={"user": self.config.username, "pwd": pwd_b64, "serviceKey": "1"},
                verify=self.config.verify_ssl,
                timeout=30,
            )
            response.raise_for_status()
        except requests.ConnectionError as e:
            raise ConnectionError(f"Cannot connect to QNAP at {self.base_url}: {e}")
        except requests.RequestException as e:
            raise ConnectionError(f"QNAP request failed: {e}")

        body = response.text
        if "<authPassed><![CDATA[1]]></authPassed>" not in body:
            raise PermissionError(
                f"QNAP authentication failed for user '{self.config.username}'"
            )

        match = re.search(r"<authSid><!\[CDATA\[(.+?)\]\]></authSid>", body)
        if not match:
            raise PermissionError(
                "QNAP authentication succeeded but no session ID returned"
            )

        self.sid = match.group(1)
        self.logger.info(f"Authenticated as {self.config.username}")

    def logout(self) -> None:
        """Log out from the QNAP NAS."""
        if not self.sid:
            return

        try:
            requests.get(
                self._auth_url(),
                params={"logout": 1, "sid": self.sid},
                verify=self.config.verify_ssl,
                timeout=10,
            )
            self.logger.info("Logged out from QNAP")
        except requests.RequestException:
            pass
        finally:
            self.sid = None

    def _ensure_authenticated(self) -> None:
        """Ensure we have a valid session."""
        if not self.sid:
            raise RuntimeError("Not authenticated. Call login() first.")

    def list_folder(self, path: str) -> List[dict]:
        """
        List contents of a folder on the QNAP NAS.

        Args:
            path: Absolute path on the NAS (e.g. /APJ-ZINW-BUFOR)

        Returns:
            List of file/folder metadata dictionaries
        """
        self._ensure_authenticated()

        response = requests.get(
            self._filemanager_url(),
            params={
                "func": "get_list",
                "sid": self.sid,
                "path": path,
                "list_mode": "all",
                "dir": "ASC",
                "limit": 1000,
                "start": 0,
                "sort": "filename",
            },
            verify=self.config.verify_ssl,
            timeout=30,
        )

        data = response.json()
        return data.get("datas", [])

    def create_folder(self, parent_path: str, folder_name: str) -> bool:
        """
        Create a folder on the QNAP NAS.

        Args:
            parent_path: Parent directory path
            folder_name: Name of the folder to create

        Returns:
            True if folder was created or already exists
        """
        self._ensure_authenticated()

        try:
            response = requests.post(
                self._filemanager_url(),
                data={
                    "func": "createdir",
                    "sid": self.sid,
                    "dest_path": parent_path,
                    "dest_folder": folder_name,
                },
                verify=self.config.verify_ssl,
                timeout=30,
            )

            data = response.json()
            status = data.get("status")
            # status 1 = success (POST), status 0 = success, status 2 = already exists
            if status in (0, 1, 2):
                return True

            self.logger.warning(
                f"Create folder returned status {status} for "
                f"{parent_path}/{folder_name}"
            )
            return False

        except requests.RequestException as e:
            self.logger.error(
                f"Failed to create folder {parent_path}/{folder_name}: {e}"
            )
            return False

    def _verify_file_exists(self, remote_dir: str, filename: str) -> bool:
        """Check if a file exists on the QNAP NAS after upload."""
        try:
            items = self.list_folder(remote_dir)
            return any(
                item.get("filename") == filename and not item.get("isfolder")
                for item in items
            )
        except Exception:
            return False

    def upload_file(self, local_path: str, remote_dir: str) -> UploadResult:
        """
        Upload a single file to the QNAP NAS.

        Args:
            local_path: Path to the local file
            remote_dir: Destination directory on the NAS

        Returns:
            UploadResult with success status
        """
        self._ensure_authenticated()

        filename = os.path.basename(local_path)
        remote_path = f"{remote_dir}/{filename}"

        try:
            with open(local_path, "rb") as f:
                response = requests.post(
                    self._filemanager_url(),
                    params={
                        "func": "upload",
                        "sid": self.sid,
                        "dest": remote_dir,
                        "type": "standard",
                        "overwrite": "0",
                    },
                    files={"file": (filename, f)},
                    verify=self.config.verify_ssl,
                    timeout=120,
                )

            data = response.json()
            if data.get("status") != 0:
                error_msg = f"Upload returned status {data.get('status')}"
                self.logger.error(f"Failed to upload {filename}: {error_msg}")
                return UploadResult(
                    file_path=local_path,
                    remote_path=remote_path,
                    success=False,
                    error=error_msg,
                )

            # Verify file actually exists on the NAS (API may return
            # success even when file write is silently denied)
            if self._verify_file_exists(remote_dir, filename):
                self.logger.info(f"Uploaded and verified: {filename} -> {remote_path}")
                return UploadResult(
                    file_path=local_path,
                    remote_path=remote_path,
                    success=True,
                )
            else:
                error_msg = (
                    "Upload API returned success but file not found on NAS. "
                    "Check that your QNAP user has file write permissions."
                )
                self.logger.error(f"Failed to upload {filename}: {error_msg}")
                return UploadResult(
                    file_path=local_path,
                    remote_path=remote_path,
                    success=False,
                    error=error_msg,
                )

        except FileNotFoundError:
            error_msg = f"Local file not found: {local_path}"
            self.logger.error(error_msg)
            return UploadResult(
                file_path=local_path,
                remote_path=remote_path,
                success=False,
                error=error_msg,
            )
        except requests.RequestException as e:
            error_msg = f"Upload request failed: {e}"
            self.logger.error(f"Failed to upload {filename}: {error_msg}")
            return UploadResult(
                file_path=local_path,
                remote_path=remote_path,
                success=False,
                error=error_msg,
            )

    def _get_month_folder(self, file_path: str) -> str:
        """
        Determine the month folder name from the invoice filename.

        Expected filename format: YYYY-MM-DD, Company, Invoice#, Description.pdf
        Falls back to file modification time if parsing fails.

        Args:
            file_path: Path to the invoice file

        Returns:
            Folder name in format MM.YYYY (e.g. "01.2026")
        """
        filename = os.path.basename(file_path)

        # Try to extract date from filename (YYYY-MM-DD at start)
        date_match = re.match(r"(\d{4})-(\d{2})-\d{2}", filename)
        if date_match:
            year = date_match.group(1)
            month = date_match.group(2)
            return f"{month}.{year}"

        # Fallback: use file modification time
        import datetime

        mtime = os.path.getmtime(file_path)
        dt = datetime.datetime.fromtimestamp(mtime)
        return f"{dt.month:02d}.{dt.year}"

    def upload_invoices(
        self,
        file_paths: List[str],
        remote_base_dir: Optional[str] = None,
    ) -> List[UploadResult]:
        """
        Upload multiple invoice files to the QNAP NAS.

        Files are organized into monthly subfolders (MM.YYYY) based on
        the invoice date in the filename.

        Args:
            file_paths: List of local file paths to upload
            remote_base_dir: Base directory on NAS (defaults to config upload_path)

        Returns:
            List of UploadResult objects
        """
        self._ensure_authenticated()

        base_dir = remote_base_dir or self.config.upload_path
        results: List[UploadResult] = []
        created_folders: set = set()

        for file_path in file_paths:
            if not os.path.isfile(file_path):
                self.logger.warning(f"Skipping non-file: {file_path}")
                results.append(
                    UploadResult(
                        file_path=file_path,
                        remote_path="",
                        success=False,
                        error=f"Not a file: {file_path}",
                    )
                )
                continue

            if self.config.organize_by_month:
                month_folder = self._get_month_folder(file_path)
                remote_dir = f"{base_dir}/{month_folder}"

                # Create month folder if we haven't already
                if month_folder not in created_folders:
                    self.logger.info(f"Ensuring folder exists: {remote_dir}")
                    self.create_folder(base_dir, month_folder)
                    created_folders.add(month_folder)
            else:
                remote_dir = base_dir

            result = self.upload_file(file_path, remote_dir)
            results.append(result)

        return results
