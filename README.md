# FCC BDC Automated Downloader + MCP Agent

Automatically detects, downloads, and ingests FCC Broadband Data Collection (BDC) releases into a local DuckDB database. Usable as a CLI script (cron-friendly) or as an MCP tool server for AI agents.

## Documentation

- [Getting Started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [CLI Reference](docs/cli-reference.md)
- [MCP Reference](docs/mcp-reference.md)
- [Operations and Troubleshooting](docs/operations.md)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Obtain FCC API credentials

1. Create a free account at <https://broadbandmap.fcc.gov/home>
2. Accept the **Public Data API Terms of Use**
3. Navigate to your account profile to retrieve your `hash_value` API token

### 3. Set environment variables

```bash
export FCC_USERNAME="your@email.com"
export FCC_API_TOKEN="your_hash_value"

# Optional overrides (defaults shown):
export BDC_DB_PATH="data/bdc.duckdb"
export BDC_MANIFEST_PATH="data/manifest.db"
export BDC_DOWNLOAD_DIR="data/downloads"
```

---

## CLI Usage

```bash
# Check for new data releases (dry-run, no downloads)
python main.py check

# Download and ingest all new releases
python main.py download

# Download a specific release
python main.py download --data-type availability --as-of-date 2024-06-30

# Re-check an already-downloaded release for late FCC corrections
python main.py download --data-type availability --as-of-date 2024-06-30 --recheck

# Show manifest progress summary
python main.py status
```

### Cron example (weekly check, every Monday at 06:00)

```cron
0 6 * * 1 cd /path/to/bsd-api-agent && python main.py download >> logs/bdc.log 2>&1
```

---

## MCP Tool Server

Run the server directly (uses stdio transport):

```bash
python mcp_server.py
```

### Claude Desktop configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bdc": {
      "command": "python",
      "args": ["/absolute/path/to/bsd-api-agent/mcp_server.py"],
      "env": {
        "FCC_USERNAME": "your@email.com",
        "FCC_API_TOKEN": "your_hash_value"
      }
    }
  }
}
```

### Available MCP tools

| Tool | Description |
|------|-------------|
| `check_new_releases` | List releases not yet in the manifest |
| `list_release_files` | Summarise files for a release (count, size, categories) |
| `download_release` | Run the full download + ingest pipeline |
| `get_download_status` | Return manifest progress summary as JSON |

---

## Data & File Layout

```
data/
├── bdc.duckdb          ← DuckDB database (all ingested data)
├── manifest.db         ← SQLite download state / resumability tracker
└── downloads/
    └── 2024-06-30/
        └── availability/
            └── State/
                └── <file>.zip
```

DuckDB table names follow the pattern `bdc_{category}_{subcategory}`, e.g.:
- `bdc_state_location_coverage`
- `bdc_provider_hexagon_coverage`
- `bdc_summary_geography`

---

## Rate Limits & Performance

- FCC API allows **~10 requests/minute** for data endpoints.
- The downloader sleeps ~6 seconds between file downloads (≤9.7 req/min).
- A full national availability release (~11,000 files) will take **18–20+ hours** sequentially. Run unattended overnight or across multiple days — the manifest enables resuming exactly where a run left off.
- Files that fail after 5 attempts are marked `error` and skipped; re-run `download` to retry them.

---

## Token Expiry / Auth Failures

If you see a 401 error:
1. Log in to <https://broadbandmap.fcc.gov/home>
2. Navigate to your account page and regenerate your token
3. Update `FCC_API_TOKEN` in your environment and restart

---

## Out of Scope

- **Location Fabric data** — requires a separate CostQuest/FCC license and is not publicly downloadable via the API. This tool does not attempt to retrieve it.
- **Cloud deployment** — designed for local Linux/server use with cron. Adapting for AWS Lambda would require splitting the pipeline due to execution time limits; AWS Batch or a VM-based cron is recommended instead.
