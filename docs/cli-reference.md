# CLI Reference

Entry point: main.py

## Command: check

Purpose:
- Compare remote FCC releases with local manifest and display unseen releases.

Usage:

```bash
python main.py check
```

Output:
- Table with data type, as-of date, and NEW status.
- Prints No new releases found if none are unseen.

## Command: download

Purpose:
- Full workflow: discover releases, queue files, download files, ingest into DuckDB, update statuses.

Usage:

```bash
python main.py download
python main.py download --data-type availability
python main.py download --as-of-date 2024-06-30
python main.py download --data-type challenge --as-of-date 2025-02-28
python main.py download --recheck
```

Options:
- --data-type: availability or challenge
- --as-of-date: release date filter (YYYY-MM-DD)
- --recheck: include already-known releases to catch late updates/corrections

Behavior details:
- Uses manifest for resumability and retry limits.
- Failed files are marked error and retried on later runs until error_count reaches 5.
- Marks a release complete when no pending/error<5 files remain for that release filter.

## Command: status

Purpose:
- Show release statuses and per-file-status counts.

Usage:

```bash
python main.py status
```

Output includes:
- Known releases table
- File count breakdown by status

## Exit behavior

- Missing credentials causes startup failure with EnvironmentError details.
- Auth failures are surfaced as permission errors and process exits with code 1.
