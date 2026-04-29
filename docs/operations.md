# Operations and Troubleshooting

## Runtime expectations

- API data endpoints are rate-limited to about 10 requests/minute.
- Full national releases can take many hours; plan for unattended execution.
- The manifest database enables restart/resume after interruption.

## Common operational commands

Check progress quickly:

```bash
python main.py status
```

Retry failed files (standard run behavior):

```bash
python main.py download
```

Force re-evaluation of previously seen releases:

```bash
python main.py download --recheck
```

## Failure modes

## 401 Unauthorized

Cause:
- Invalid, expired, or revoked FCC API token.

Action:
1. Log into broadbandmap.fcc.gov
2. Regenerate/retrieve hash_value token
3. Update FCC_API_TOKEN
4. Re-run command

## 403 Forbidden

Cause:
- Access denied for account/token to requested resource.

Action:
- Verify account access and ToU acceptance for API usage.

## 429 Too Many Requests

Cause:
- Rate limit reached.

Behavior:
- Client/downloader sleeps and retries.

Action:
- Avoid running multiple downloader processes against the same token.

## MD5 mismatch

Cause:
- Partial/corrupt transfer.

Behavior:
- Temp file removed and file is retried with backoff.

Action:
- Usually none; if persistent, retry later.

## Ingestion schema drift

Cause:
- CSV introduces new columns.

Behavior:
- Ingest layer adds missing columns via ALTER TABLE and continues.

## Data maintenance

The downloader stores both raw downloaded files and structured DuckDB tables.
If storage is constrained:
- Move old raw downloads to archive storage.
- Keep manifest.db and bdc.duckdb on fast local disk.

## Safe deployment pattern

- Run one process per environment.
- Use cron/systemd timer for schedule.
- Redirect stdout/stderr to a rolling log destination.
- Monitor file_counts in status output for growth in error state.
