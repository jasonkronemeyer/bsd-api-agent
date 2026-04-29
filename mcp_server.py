#!/usr/bin/env python3
"""
MCP tool server for the FCC BDC downloader.

Exposes 4 tools over stdio transport (compatible with Claude Desktop,
VS Code agent mode, and any MCP-compliant client):

    check_new_releases      — discover new data releases from the FCC API
    list_release_files      — paginate and summarise files for a release
    download_release        — run the full download + ingest pipeline
    get_download_status     — query the manifest for current progress

Run directly:
    python mcp_server.py

Or configure in claude_desktop_config.json:
    {
      "mcpServers": {
        "bdc": {
          "command": "python",
          "args": ["/path/to/bsd-api-agent/mcp_server.py"],
          "env": {
            "FCC_USERNAME": "your@email.com",
            "FCC_API_TOKEN": "your_hash_value"
          }
        }
      }
    }
"""

import json
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from bdc.config import load_config
from bdc.client import BDCClient
from bdc.state import Manifest
from bdc.downloader import Downloader, DownloadError
from bdc.ingest import ingest_file

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("bdc.mcp")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: list[Tool] = [
    Tool(
        name="check_new_releases",
        description=(
            "Query the FCC BDC API for available data releases and return any that "
            "have not yet been downloaded. Returns a list of {data_type, as_of_date} objects."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="list_release_files",
        description=(
            "List the files available for a specific BDC release. "
            "Returns a summary with file counts, total size, and category breakdown."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_type": {
                    "type": "string",
                    "enum": ["availability", "challenge"],
                    "description": "The type of BDC data.",
                },
                "as_of_date": {
                    "type": "string",
                    "description": "The release date in YYYY-MM-DD format.",
                },
            },
            "required": ["data_type", "as_of_date"],
        },
    ),
    Tool(
        name="download_release",
        description=(
            "Trigger the full download and DuckDB ingestion pipeline for a BDC release. "
            "This is a long-running operation (potentially hours for a full national dataset). "
            "Returns a progress summary after completion or on error."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_type": {
                    "type": "string",
                    "enum": ["availability", "challenge"],
                    "description": "The type of BDC data. Omit to process both types.",
                },
                "as_of_date": {
                    "type": "string",
                    "description": "The release date in YYYY-MM-DD format. Omit to process all new dates.",
                },
                "recheck": {
                    "type": "boolean",
                    "description": "Re-queue files for already-seen dates to catch late FCC corrections.",
                    "default": False,
                },
            },
        },
    ),
    Tool(
        name="get_download_status",
        description=(
            "Return the current manifest status: known releases and file counts "
            "broken down by status (pending, downloading, downloaded, ingested, error)."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


# ---------------------------------------------------------------------------
# Handler logic (shared with CLI to avoid duplication)
# ---------------------------------------------------------------------------

def _handle_check_new_releases(client: BDCClient, manifest: Manifest) -> dict:
    known = {(r["data_type"], r["as_of_date"]) for r in manifest.get_known_dates()}
    remote = client.list_as_of_dates()
    new_dates = [
        d for d in remote
        if (d.get("data_type"), d.get("as_of_date")) not in known
    ]
    return {"new_releases": new_dates, "total_new": len(new_dates)}


def _handle_list_release_files(
    client: BDCClient, data_type: str, as_of_date: str
) -> dict:
    if data_type == "availability":
        files = list(client.iter_availability_files(as_of_date))
    else:
        files = list(client.iter_challenge_files(as_of_date))

    total_size = sum(
        f.get("file_size") or f.get("fileSize") or 0 for f in files
    )
    categories: dict[str, int] = {}
    for f in files:
        cat = f.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "data_type": data_type,
        "as_of_date": as_of_date,
        "total_files": len(files),
        "total_size_bytes": total_size,
        "total_size_gb": round(total_size / 1e9, 2),
        "by_category": categories,
    }


def _handle_download_release(
    client: BDCClient,
    manifest: Manifest,
    downloader: Downloader,
    cfg: dict,
    data_type: str | None,
    as_of_date: str | None,
    recheck: bool,
) -> dict:
    from main import _queue_files_for_date

    all_remote = client.list_as_of_dates()
    known = {(r["data_type"], r["as_of_date"]) for r in manifest.get_known_dates()}

    dates_to_process: list[tuple[str, str]] = []
    for entry in all_remote:
        dt = entry.get("data_type", "")
        aod = entry.get("as_of_date", "")
        if data_type and dt != data_type:
            continue
        if as_of_date and aod != as_of_date:
            continue
        if (dt, aod) not in known or recheck:
            manifest.upsert_date(dt, aod)
            dates_to_process.append((dt, aod))

    if not dates_to_process:
        return {"message": "Nothing new to download.", "processed": 0}

    for dt, aod in dates_to_process:
        _queue_files_for_date(client, manifest, dt, aod)

    pending = manifest.get_pending_files(as_of_date=as_of_date, data_type=data_type)
    errors = 0
    downloaded = 0
    ingested = 0

    for file_meta in pending:
        try:
            local_path = downloader.download_file(file_meta)
            downloaded += 1
            file_meta["local_path"] = local_path
            ingest_file(file_meta, manifest, cfg["db_path"])
            ingested += 1
        except DownloadError:
            errors += 1

    for dt, aod in dates_to_process:
        if not manifest.get_pending_files(as_of_date=aod, data_type=dt):
            manifest.mark_date_complete(dt, aod)

    return {
        "releases_processed": len(dates_to_process),
        "files_downloaded": downloaded,
        "files_ingested": ingested,
        "files_errored": errors,
    }


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

async def main() -> None:
    try:
        cfg = load_config()
    except EnvironmentError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    client = BDCClient(cfg["username"], cfg["token"])
    manifest = Manifest(cfg["manifest_path"])
    downloader = Downloader(client, manifest, cfg["download_dir"])

    server = Server("bdc-downloader")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "check_new_releases":
                result = _handle_check_new_releases(client, manifest)

            elif name == "list_release_files":
                result = _handle_list_release_files(
                    client,
                    arguments["data_type"],
                    arguments["as_of_date"],
                )

            elif name == "download_release":
                result = _handle_download_release(
                    client=client,
                    manifest=manifest,
                    downloader=downloader,
                    cfg=cfg,
                    data_type=arguments.get("data_type"),
                    as_of_date=arguments.get("as_of_date"),
                    recheck=arguments.get("recheck", False),
                )

            elif name == "get_download_status":
                result = manifest.summary()

            else:
                result = {"error": f"Unknown tool: {name}"}

        except PermissionError as exc:
            result = {"error": "auth_failure", "detail": str(exc)}
        except Exception as exc:
            logger.exception("Tool %s raised an unexpected error.", name)
            result = {"error": type(exc).__name__, "detail": str(exc)}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
