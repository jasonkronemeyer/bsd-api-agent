## Plan: FCC BDC Automated Downloader + MCP Agent

**TL;DR**: Build a Python package with a core BDC API library that powers both a resumable CLI download script (for cron) and an MCP tool server (for AI agent use). Downloads the full national dataset (~11k+ files/release) with rate-limited streaming, MD5 verification, and DuckDB ingestion. State is tracked in SQLite for resumability across the multi-hour download window.

---

### Directory Layout

```
bsd-api-agent/
├── bdc/
│   ├── config.py        ← env var loading (FCC_USERNAME, FCC_API_TOKEN)
│   ├── client.py        ← API wrapper: listAsOfDates, paginated list, download stream
│   ├── downloader.py    ← streaming save, MD5 verify, retry, rate limiter
│   ├── ingest.py        ← unzip CSV → DuckDB COPY
│   └── state.py         ← SQLite manifest: dates + files + status
├── mcp_server.py        ← MCP tool server backed by bdc library
├── main.py              ← CLI: check / download / status subcommands
├── requirements.txt
└── README.md
```

---

### Phase 1 — Core Infrastructure

1. **`bdc/config.py`** — load `FCC_USERNAME`, `FCC_API_TOKEN`, `BDC_DB_PATH`, `BDC_MANIFEST_PATH` from env; raise `EnvironmentError` on missing credentials
2. **`bdc/state.py`** — SQLite manifest with two tables: `as_of_dates` (data_type, date, status) and `files` (file_id, name, size, md5, status, error_count). File-lock on writes to prevent concurrent runs
3. **`bdc/client.py`** — `requests.Session` with `username`/`hash_value` headers; methods: `list_as_of_dates()`, `list_availability_data(date, page, per_page)`, `list_challenge_data(date, page, per_page)`, `download_file(file_id)` → returns streaming response

### Phase 2 — Download Manager *(depends on Phase 1)*

4. **`bdc/downloader.py`** — per-file logic: write to `.tmp` file in chunks → compute MD5 on completion → compare to metadata → rename to final path on match; retry via `tenacity` with exponential backoff; 6-second inter-request sleep (enforces ≤10 req/min); on HTTP 429, respect `Retry-After` header

### Phase 3 — DuckDB Ingestion *(depends on Phase 2)*

5. **`bdc/ingest.py`** — unzip downloaded file, read CSV header row, `CREATE TABLE IF NOT EXISTS` in DuckDB named by `{category}_{subcategory}`, `COPY` from CSV; if schema mismatch (new columns), `ALTER TABLE ADD COLUMN` and log warning; mark file `ingested` in manifest

### Phase 4 — CLI Orchestration *(depends on Phases 1–3)*

6. **`main.py`** — `argparse` with subcommands:
   - `check` — calls `list_as_of_dates()`, prints new unretrieved dates
   - `download [--data-type TYPE] [--as-of-date DATE]` — full pipeline: discover → paginate list → queue in manifest → stream download → ingest → mark done
   - `status` — prints manifest summary table (counts by status)

### Phase 5 — MCP Tool Server *(depends on Phase 4, parallel with step 6 after Phase 3)*

7. **`mcp_server.py`** — using the `mcp` Python package (stdio transport), expose 4 tools:
   - `check_new_releases` → calls `list_as_of_dates()`
   - `list_release_files(data_type, as_of_date)` → paginates listing, returns summary
   - `download_release(data_type, as_of_date)` → triggers full download+ingest pipeline
   - `get_download_status` → queries manifest, returns JSON summary

### Phase 6 — Packaging

8. **`requirements.txt`**: `requests`, `tenacity`, `duckdb`, `mcp`, `tqdm`
9. **`README.md`**: env var setup, first-run steps (get FCC token), cron example, MCP config snippet for Claude Desktop

---

### Verification
1. Mock `requests` with `responses` library to unit-test `client.py` (list pagination, 429 handling)
2. Live smoke test with a small real subset — challenge data for one recent month
3. Confirm DuckDB row count matches line count of ingested CSV
4. Run MCP server, invoke `check_new_releases` via MCP Inspector
5. Interrupt a download mid-way; re-run and confirm it resumes (skips completed files, retries partial)
6. Confirm no more than 10 API calls/minute under normal download run

---

### Decisions & Scope
- **In scope**: availability data, challenge data, full national set, DuckDB ingestion
- **Out of scope**: location fabric data (requires separate FCC license), cloud deployment, async concurrency (sequential + sleep is sufficient given rate limit)
- DuckDB file: `data/bdc.duckdb`; manifest: `data/manifest.db`
- Single-instance safety via SQLite write lock
- MCP server uses stdio transport — compatible with Claude Desktop and VS Code agent mode

---

### Further Considerations

1. **Partial release updates**: FCC sometimes adds/corrects files for an already-released date. The manifest should re-check the latest release's file list for a configurable "freshness window" (e.g. 30 days after initial download). Include as a `--recheck` flag on the `download` subcommand.

2. **Token expiry**: The FCC token has no documented TTL, but could be revoked. Test auth on startup and surface a clear error with renewal instructions on 401s rather than silently failing mid-download.
