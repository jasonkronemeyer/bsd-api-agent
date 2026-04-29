"""
Configuration loader for the FCC BDC downloader.

Required environment variables:
    FCC_USERNAME       Your FCC BDC account username (email address).
    FCC_API_TOKEN      The hash_value token from your FCC BDC account.

Optional environment variables:
    BDC_DB_PATH        Path to the DuckDB database file. Default: data/bdc.duckdb
    BDC_MANIFEST_PATH  Path to the SQLite manifest file.  Default: data/manifest.db
    BDC_DOWNLOAD_DIR   Directory for temporary download staging. Default: data/downloads
"""

import os


def load_config() -> dict:
    """Load and validate configuration from environment variables.

    Raises:
        EnvironmentError: If required credentials are missing.

    Returns:
        dict with keys: username, token, db_path, manifest_path, download_dir
    """
    username = os.environ.get("FCC_USERNAME", "").strip()
    token = os.environ.get("FCC_API_TOKEN", "").strip()

    if not username or not token:
        raise EnvironmentError(
            "FCC_USERNAME and FCC_API_TOKEN environment variables must be set.\n"
            "Steps to obtain credentials:\n"
            "  1. Create an account at https://broadbandmap.fcc.gov/home\n"
            "  2. Accept the Terms of Use for the Public Data API\n"
            "  3. Navigate to your account page to retrieve your hash_value token\n"
            "  4. Export the values before running:\n"
            "       export FCC_USERNAME='your@email.com'\n"
            "       export FCC_API_TOKEN='your_hash_value'"
        )

    return {
        "username": username,
        "token": token,
        "db_path": os.environ.get("BDC_DB_PATH", "data/bdc.duckdb"),
        "manifest_path": os.environ.get("BDC_MANIFEST_PATH", "data/manifest.db"),
        "download_dir": os.environ.get("BDC_DOWNLOAD_DIR", "data/downloads"),
    }
