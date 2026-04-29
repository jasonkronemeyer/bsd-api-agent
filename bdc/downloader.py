"""
Download manager: streams a single BDC file to disk with MD5 verification,
rate limiting, and retry-with-exponential-backoff via tenacity.

Design:
  - Write to <dest>.tmp, compute MD5 inline, rename to <dest> on success.
  - Sleep _INTER_REQUEST_SLEEP seconds between download calls to stay ≤10 req/min.
  - Respect Retry-After header on 429 responses.
  - On any failure, clean up the .tmp file and raise so tenacity can retry.
"""

import hashlib
import logging
import os
import time

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm

from .client import BDCClient
from .state import Manifest

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB
_INTER_REQUEST_SLEEP = 6.2     # seconds → ≤ ~9.7 requests/minute
_MAX_ATTEMPTS = 5


class DownloadError(Exception):
    """Raised when a file download fails unrecoverably (e.g. bad MD5)."""


class _RetryableError(Exception):
    """Raised for transient failures that should trigger a tenacity retry."""


def _make_retry_decorator():
    return retry(
        retry=retry_if_exception_type(_RetryableError),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=2, min=10, max=120),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


class Downloader:
    def __init__(self, client: BDCClient, manifest: Manifest, download_dir: str):
        self._client = client
        self._manifest = manifest
        self._download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)

    def download_file(self, file_meta: dict) -> str:
        """Download a single file, verify MD5, return local path.

        Updates manifest status throughout. On success the manifest entry
        is set to 'downloaded'. On terminal failure sets 'error'.

        Args:
            file_meta: dict with keys file_id, file_name, file_size, md5,
                       as_of_date, data_type, category, subcategory.

        Returns:
            Absolute path to the saved file.

        Raises:
            DownloadError: if download fails after all retries.
        """
        file_id = file_meta["file_id"]
        file_name = file_meta.get("file_name") or file_id
        as_of_date = file_meta.get("as_of_date", "unknown")

        dest_dir = os.path.join(
            self._download_dir,
            as_of_date,
            file_meta.get("data_type", "unknown"),
            file_meta.get("category", "unknown"),
        )
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, file_name)
        tmp_path = dest_path + ".tmp"

        if os.path.exists(dest_path):
            logger.debug("Already exists, skipping: %s", dest_path)
            return dest_path

        self._manifest.mark_downloading(file_id)
        try:
            self._attempt_download(file_meta, dest_path, tmp_path)
        except (_RetryableError, DownloadError) as exc:
            self._manifest.mark_error(file_id, str(exc))
            raise DownloadError(f"Failed to download {file_name}: {exc}") from exc

        self._manifest.mark_downloaded(file_id, dest_path)
        return dest_path

    @_make_retry_decorator()
    def _attempt_download(self, file_meta: dict, dest_path: str, tmp_path: str) -> None:
        file_id = file_meta["file_id"]
        expected_md5 = file_meta.get("md5")
        expected_size = file_meta.get("file_size")
        file_name = file_meta.get("file_name") or file_id

        try:
            resp = self._client.download_file_stream(file_id)
        except PermissionError:
            raise  # not retryable

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning("429 on download of %s; sleeping %ds", file_name, retry_after)
            time.sleep(retry_after)
            raise _RetryableError(f"Rate limited downloading {file_name}")

        if not resp.ok:
            raise _RetryableError(
                f"HTTP {resp.status_code} downloading {file_name}"
            )

        hasher = hashlib.md5()
        bytes_written = 0

        try:
            with open(tmp_path, "wb") as fh:
                with tqdm(
                    total=expected_size,
                    unit="B",
                    unit_scale=True,
                    desc=file_name[:50],
                    leave=False,
                ) as bar:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)
                            hasher.update(chunk)
                            bytes_written += len(chunk)
                            bar.update(len(chunk))
        except (requests.RequestException, OSError) as exc:
            _cleanup(tmp_path)
            raise _RetryableError(f"I/O error downloading {file_name}: {exc}") from exc
        finally:
            resp.close()

        # Verify MD5 if provided.
        if expected_md5:
            actual_md5 = hasher.hexdigest()
            if actual_md5.lower() != expected_md5.lower():
                _cleanup(tmp_path)
                raise _RetryableError(
                    f"MD5 mismatch for {file_name}: expected {expected_md5}, got {actual_md5}"
                )

        # Atomic rename.
        os.replace(tmp_path, dest_path)
        logger.info(
            "Downloaded %s (%s bytes)", file_name, f"{bytes_written:,}"
        )

        # Throttle after each successful download.
        time.sleep(_INTER_REQUEST_SLEEP)


def _cleanup(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("Could not clean up temp file %s: %s", path, exc)
