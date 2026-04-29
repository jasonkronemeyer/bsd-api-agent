# Getting Started

## What this project does

This project automates FCC Broadband Data Collection (BDC) retrieval using the Public Data API and ingests downloaded CSV data into a local DuckDB database.

Main capabilities:
- Discover new BDC releases by data type and as-of date.
- Queue and download release files with retry and rate-limit safety.
- Verify file integrity using MD5 checksums when provided.
- Ingest CSV payloads into DuckDB with basic schema evolution.
- Expose the same pipeline via CLI and MCP tools.

## Prerequisites

- Python 3.10+ recommended
- Linux shell access for scheduled runs
- FCC account with Public Data API token

## Install

```bash
pip install -r requirements.txt
```

## Configure environment variables

Required:

```bash
export FCC_USERNAME="your@email.com"
export FCC_API_TOKEN="your_hash_value"
```

Optional (defaults shown):

```bash
export BDC_DB_PATH="data/bdc.duckdb"
export BDC_MANIFEST_PATH="data/manifest.db"
export BDC_DOWNLOAD_DIR="data/downloads"
```

## First run

1. Verify credentials and check for new releases:

```bash
python main.py check
```

2. Download and ingest all new releases:

```bash
python main.py download
```

3. Inspect current state:

```bash
python main.py status
```

## Recommended first production schedule

Run weekly and let the manifest handle resume/retry:

```cron
0 6 * * 1 cd /path/to/bsd-api-agent && python main.py download >> logs/bdc.log 2>&1
```
