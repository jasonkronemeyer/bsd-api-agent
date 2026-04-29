## What This Project Does

The FCC publishes broadband availability data twice a year — a massive dataset showing where internet service exists across the entire United States. This data is publicly accessible through an official API, but downloading it manually is impractical: each release can contain over 11,000 individual files totaling dozens of gigabytes.

This project automates that entire process.

---

## The Problem It Solves

1. **Detection** — You shouldn't have to manually check if new data has been published. The tool checks the FCC's API on a schedule and detects new releases automatically.

2. **Downloading** — The FCC limits how fast you can request data (~10 files per minute). A full national download takes 18–20+ hours. The tool handles this unattended, respects the rate limit, and resumes exactly where it left off if anything interrupts it.

3. **Integrity** — Every file has a checksum provided by the FCC. The tool verifies each download matches that checksum before accepting it. Corrupt or incomplete files are retried automatically.

4. **Storage** — Raw CSV files are extracted and loaded into a DuckDB database — a fast, queryable local database that makes it easy to run SQL queries against broadband availability data without needing a server.

5. **Resumability** — A SQLite "manifest" database tracks the status of every file (queued, downloading, downloaded, ingested, or failed). If the process is interrupted, the next run picks up exactly where it left off.

---

## Two Ways to Use It

**As a CLI script (automated/scheduled)**
Run it from the command line or on a weekly cron job. Three commands cover everything:
- `check` — look for new releases
- `download` — run the full pipeline
- `status` — see current progress

**As an MCP tool server (AI agent integration)**
The same functionality is also exposed as a set of tools an AI assistant can call directly — check for new data, inspect a release, trigger a download, or ask for status — all from a conversation.

---

## How the Code Is Organized

| File/Folder | Role |
|---|---|
| `bdc/config.py` | Reads credentials from environment variables |
| `bdc/client.py` | Talks to the FCC API |
| `bdc/state.py` | Tracks what has been downloaded (SQLite) |
| `bdc/downloader.py` | Downloads files safely with retry and verification |
| `bdc/ingest.py` | Loads downloaded files into DuckDB |
| `main.py` | CLI entry point |
| `mcp_server.py` | AI agent tool server |

---

## What It Does Not Do

- It does not access the **Location Fabric** (the precise address-level dataset) — that requires a separate government license.
- It is not designed for cloud deployment out of the box — it runs on a local Linux machine or server.