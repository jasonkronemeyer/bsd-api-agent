"""
FCC BDC Public Data API client.

Reference: FCC National Broadband Map Public Data API Specifications v1.5
Base URL:  https://broadbandmap.fcc.gov/api/public/map/

Authentication: every request requires headers:
    username   — your FCC BDC account username (email)
    hash_value — your API token

Rate limit: ~10 requests/minute for data endpoints.
"""

import logging
import time
from typing import Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_BASE_URL = "https://broadbandmap.fcc.gov/api/public/map"
_DEFAULT_PER_PAGE = 1000
_REQUEST_TIMEOUT = 60  # seconds


def _build_session(username: str, token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"username": username, "hash_value": token})
    # Retry on connection errors and 5xx, but NOT on 429 (handled manually).
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class BDCClient:
    """Thin wrapper around the FCC BDC Public Data API."""

    def __init__(self, username: str, token: str):
        self._username = username
        self._session = _build_session(username, token)

    # ------------------------------------------------------------------
    # Auth check
    # ------------------------------------------------------------------

    def verify_auth(self) -> None:
        """Call listAsOfDates to confirm credentials are valid.

        Raises:
            PermissionError: if the server returns 401/403.
            requests.HTTPError: on other non-2xx responses.
        """
        resp = self._get(f"{_BASE_URL}/listAsOfDates")
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_as_of_dates(self) -> list[dict]:
        """Return all available (data_type, as_of_date) pairs.

        Each dict has at minimum: data_type, as_of_date.
        """
        resp = self._get(f"{_BASE_URL}/listAsOfDates")
        resp.raise_for_status()
        return resp.json().get("data", [])

    # ------------------------------------------------------------------
    # File listing (paginated)
    # ------------------------------------------------------------------

    def iter_availability_files(self, as_of_date: str) -> Iterator[dict]:
        """Yield all file metadata entries for a given availability as_of_date."""
        yield from self._iter_file_listing(f"{_BASE_URL}/listAvailabilityData/{as_of_date}")

    def iter_challenge_files(self, as_of_date: str) -> Iterator[dict]:
        """Yield all file metadata entries for a given challenge as_of_date."""
        yield from self._iter_file_listing(f"{_BASE_URL}/listChallengeData/{as_of_date}")

    def _iter_file_listing(self, url: str) -> Iterator[dict]:
        page = 1
        while True:
            params = {"page": page, "per_page": _DEFAULT_PER_PAGE}
            resp = self._get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("data", [])
            if not items:
                break
            yield from items
            # If fewer items than requested, we've hit the last page.
            if len(items) < _DEFAULT_PER_PAGE:
                break
            page += 1

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file_stream(self, file_id: str) -> requests.Response:
        """Return a streaming Response for the given file_id.

        The caller is responsible for reading and closing the response.
        Does NOT retry automatically — the downloader handles retry logic.
        """
        url = f"{_BASE_URL}/download/{file_id}"
        resp = self._session.get(url, stream=True, timeout=(_REQUEST_TIMEOUT, None))
        if resp.status_code == 401:
            raise PermissionError(
                "API returned 401 Unauthorized. Your FCC token may have expired or been revoked.\n"
                "Visit https://broadbandmap.fcc.gov/home to renew your token, then update FCC_API_TOKEN."
            )
        if resp.status_code == 403:
            raise PermissionError(f"API returned 403 Forbidden for file_id={file_id}.")
        return resp

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        resp = self._session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 401:
            raise PermissionError(
                "API returned 401 Unauthorized. Your FCC token may have expired or been revoked.\n"
                "Visit https://broadbandmap.fcc.gov/home to renew your token, then update FCC_API_TOKEN."
            )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning("Rate limited (429). Sleeping %d seconds.", retry_after)
            time.sleep(retry_after)
            # One automatic retry after the backoff.
            resp = self._session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        return resp
