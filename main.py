#!/usr/bin/env python3
"""
FCC BDC Automated Downloader — CLI entry point.

Subcommands:
    check      List new (unretrieved) as-of dates from the FCC API.
    download   Discover → queue → download → ingest files for a release.
    status     Print a summary of the manifest (dates and file counts).

Usage examples:
    python main.py check
    python main.py download
    python main.py download --data-type availability --as-of-date 2024-06-30
    python main.py download --data-type availability --recheck
    python main.py status
"""

import argparse
import logging
import sys
from typing import Optional

from bdc.config import load_config
from bdc.client import BDCClient
from bdc.state import Manifest
from bdc.downloader import Downloader, DownloadError
from bdc.ingest import ingest_file

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bdc.main")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_clients(cfg: dict) -> tuple[BDCClient, Manifest, Downloader]:
    client = BDCClient(cfg["username"], cfg["token"])
    manifest = Manifest(cfg["manifest_path"])
    downloader = Downloader(client, manifest, cfg["download_dir"])
    return client, manifest, downloader


def _queue_files_for_date(
    client: BDCClient,
    manifest: Manifest,
    data_type: str,
    as_of_date: str,
) -> int:
    """Paginate the file listing and upsert all entries into the manifest.

    Returns the count of newly added files.
    """
    if data_type == "availability":
        iterator = client.iter_availability_files(as_of_date)
    elif data_type == "challenge":
        iterator = client.iter_challenge_files(as_of_date)
    else:
        logger.warning("Unknown data_type '%s'; skipping.", data_type)
        return 0

    new_count = 0
    for entry in iterator:
        # Normalise field names — FCC API uses camelCase or snake_case depending on version.
        file_meta = {
            "file_id":     entry.get("file_id") or entry.get("fileId") or "",
            "data_type":   data_type,
            "as_of_date":  as_of_date,
            "file_name":   entry.get("file_name") or entry.get("fileName"),
            "file_size":   entry.get("file_size") or entry.get("fileSize"),
            "md5":         entry.get("md5_checksum") or entry.get("md5Checksum") or entry.get("md5"),
            "category":    entry.get("category"),
            "subcategory": entry.get("subcategory"),
        }
        if not file_meta["file_id"]:
            logger.warning("Skipping entry with no file_id: %s", entry)
            continue
        if manifest.upsert_file(file_meta):
            new_count += 1

    logger.info(
        "Queued %d new file(s) for %s / %s.", new_count, data_type, as_of_date
    )
    return new_count


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace, cfg: dict) -> None:
    """List new as-of dates not yet in the manifest."""
    client = BDCClient(cfg["username"], cfg["token"])

    logger.info("Verifying API credentials …")
    try:
        client.verify_auth()
    except PermissionError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    manifest = Manifest(cfg["manifest_path"])
    known = {(r["data_type"], r["as_of_date"]) for r in manifest.get_known_dates()}

    remote_dates = client.list_as_of_dates()
    new_dates = [
        d for d in remote_dates
        if (d.get("data_type"), d.get("as_of_date")) not in known
    ]

    if not new_dates:
        print("No new releases found.")
        return

    print(f"{'DATA TYPE':<20} {'AS-OF DATE':<15} STATUS")
    print("-" * 50)
    for d in sorted(new_dates, key=lambda x: x.get("as_of_date", "")):
        print(f"{d.get('data_type','?'):<20} {d.get('as_of_date','?'):<15} NEW")


def cmd_download(args: argparse.Namespace, cfg: dict) -> None:
    """Discover, queue, download, and ingest BDC files."""
    client, manifest, downloader = _build_clients(cfg)

    logger.info("Verifying API credentials …")
    try:
        client.verify_auth()
    except PermissionError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # ---- Step 1: Discover new as-of dates ----
    all_remote = client.list_as_of_dates()
    known = {(r["data_type"], r["as_of_date"]) for r in manifest.get_known_dates()}

    dates_to_process: list[tuple[str, str]] = []
    for entry in all_remote:
        dt = entry.get("data_type", "")
        aod = entry.get("as_of_date", "")
        if not dt or not aod:
            continue
        if args.data_type and dt != args.data_type:
            continue
        if args.as_of_date and aod != args.as_of_date:
            continue

        if (dt, aod) not in known or args.recheck:
            manifest.upsert_date(dt, aod)
            dates_to_process.append((dt, aod))

    if not dates_to_process:
        print("Nothing new to download.")
        return

    logger.info("Processing %d release(s).", len(dates_to_process))

    # ---- Step 2: Queue files for each date ----
    for data_type, as_of_date in dates_to_process:
        logger.info("Listing files for %s / %s …", data_type, as_of_date)
        try:
            _queue_files_for_date(client, manifest, data_type, as_of_date)
        except Exception as exc:
            logger.error("Failed to list files for %s/%s: %s", data_type, as_of_date, exc)
            continue

    # ---- Step 3: Download pending files ----
    pending = manifest.get_pending_files(
        as_of_date=args.as_of_date or None,
        data_type=args.data_type or None,
    )
    logger.info("Downloading %d pending file(s) …", len(pending))

    errors = 0
    for file_meta in pending:
        try:
            local_path = downloader.download_file(file_meta)
        except DownloadError as exc:
            logger.error("Download failed (will retry next run): %s", exc)
            errors += 1
            continue

        # ---- Step 4: Ingest into DuckDB ----
        file_meta["local_path"] = local_path
        ingest_file(file_meta, manifest, cfg["db_path"])

    # ---- Step 5: Mark complete dates ----
    for data_type, as_of_date in dates_to_process:
        remaining = manifest.get_pending_files(as_of_date=as_of_date, data_type=data_type)
        if not remaining:
            manifest.mark_date_complete(data_type, as_of_date)
            logger.info("Marked %s / %s as complete.", data_type, as_of_date)

    if errors:
        logger.warning(
            "%d file(s) failed and will be retried on the next run.", errors
        )
    else:
        logger.info("All files processed successfully.")


def cmd_status(args: argparse.Namespace, cfg: dict) -> None:
    """Print a summary of the manifest."""
    manifest = Manifest(cfg["manifest_path"])
    summary = manifest.summary()

    print("\n=== Known Releases ===")
    print(f"{'DATA TYPE':<20} {'AS-OF DATE':<15} {'STATUS':<10}")
    print("-" * 50)
    for d in summary["dates"]:
        print(f"{d['data_type']:<20} {d['as_of_date']:<15} {d['status']:<10}")

    print("\n=== File Counts by Status ===")
    counts = summary["file_counts"]
    if not counts:
        print("  (no files queued yet)")
    else:
        total = sum(counts.values())
        for status, n in sorted(counts.items()):
            bar = "█" * int(40 * n / total) if total else ""
            print(f"  {status:<12} {n:>8,}  {bar}")
        print(f"  {'TOTAL':<12} {total:>8,}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bdc",
        description="FCC BDC automated downloader and DuckDB ingestion pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # check
    sub.add_parser("check", help="List new releases not yet in the manifest.")

    # download
    dl = sub.add_parser("download", help="Download and ingest BDC data.")
    dl.add_argument(
        "--data-type",
        choices=["availability", "challenge"],
        default=None,
        help="Limit to one data type (default: both).",
    )
    dl.add_argument(
        "--as-of-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Limit to a specific as-of date.",
    )
    dl.add_argument(
        "--recheck",
        action="store_true",
        help="Re-queue files for already-seen dates (catch late FCC corrections).",
    )

    # status
    sub.add_parser("status", help="Print manifest summary.")

    args = parser.parse_args()

    try:
        cfg = load_config()
    except EnvironmentError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    dispatch = {
        "check": cmd_check,
        "download": cmd_download,
        "status": cmd_status,
    }
    dispatch[args.command](args, cfg)


if __name__ == "__main__":
    main()
