# Architecture

## Pipeline overview

The runtime flow is:
1. Load config from environment.
2. Verify FCC API auth.
3. Discover available releases from listAsOfDates.
4. Queue release files into SQLite manifest from paginated listing endpoints.
5. Download each pending file with retry, throttling, and MD5 verification.
6. Ingest extracted CSVs into DuckDB tables.
7. Mark file and release status in manifest.

## Components

## Config loader
- File: bdc/config.py
- Responsibility: validate required environment variables and provide default paths.

## API client
- File: bdc/client.py
- Responsibility: authenticated HTTP session, list/discovery endpoints, paginated iteration, streaming download call.
- Key behavior: handles 401/403 as permission errors, applies one retry after 429 with Retry-After sleep.

## Manifest/state layer
- File: bdc/state.py
- Storage: SQLite (WAL mode)
- Tables:
  - as_of_dates: one row per data_type + as_of_date
  - files: one row per file_id with status and metadata
- Resilience behavior: stale downloading states are reset to pending on process startup.

## Downloader
- File: bdc/downloader.py
- Behavior:
  - streams to .tmp file
  - computes MD5 during write
  - renames atomically on success
  - retries transient failures (tenacity exponential backoff)
  - enforces ~10 req/min by sleeping ~6.2s between successful downloads

## Ingestion
- File: bdc/ingest.py
- Behavior:
  - supports zip, gz, and csv
  - infers table name from category + subcategory
  - creates table if absent
  - adds newly observed columns via ALTER TABLE
  - appends rows using read_csv_auto

## CLI orchestrator
- File: main.py
- Exposes subcommands: check, download, status.
- Drives the end-to-end workflow for unattended scheduled execution.

## MCP server
- File: mcp_server.py
- Exposes tool-based control over the same core pipeline.
- Designed for stdio transport in MCP-compatible clients.

## Data layout

Typical output layout:

```text
data/
├── bdc.duckdb
├── manifest.db
└── downloads/
    └── <as_of_date>/<data_type>/<category>/<file_name>
```

## Status model

File statuses in manifest:
- pending
- downloading
- downloaded
- ingested
- error

Release status in as_of_dates:
- pending
- complete
