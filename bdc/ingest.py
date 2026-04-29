"""
DuckDB ingestion module.

For each downloaded file:
  1. Detect format (zip or plain CSV/gz).
  2. Extract to a temporary CSV if needed.
  3. Read the header row to determine the schema.
  4. CREATE TABLE IF NOT EXISTS in DuckDB named after category + subcategory.
  5. COPY (append) the CSV rows into the table.
  6. Handle schema evolution: if new columns appear, ALTER TABLE ADD COLUMN.
  7. Mark the file as 'ingested' in the manifest.

Table naming: category and subcategory values are lower-cased, spaces/hyphens
replaced with underscores, prefixed with 'bdc_'.  E.g.:
  category="State", subcategory="Location Coverage" → bdc_state_location_coverage
"""

import csv
import io
import logging
import os
import re
import zipfile
import gzip
import shutil
import tempfile
from typing import Optional

import duckdb

from .state import Manifest

logger = logging.getLogger(__name__)


def _table_name(category: Optional[str], subcategory: Optional[str]) -> str:
    parts = [category or "unknown", subcategory or "unknown"]
    clean = "_".join(
        re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_") for p in parts
    )
    return f"bdc_{clean}"


def _extract_csv(local_path: str, tmp_dir: str) -> list[str]:
    """Extract local_path to tmp_dir and return a list of CSV file paths."""
    csv_paths: list[str] = []
    lower = local_path.lower()

    if lower.endswith(".zip"):
        with zipfile.ZipFile(local_path, "r") as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    zf.extract(name, tmp_dir)
                    csv_paths.append(os.path.join(tmp_dir, name))
    elif lower.endswith(".gz"):
        out_name = os.path.splitext(os.path.basename(local_path))[0]
        out_path = os.path.join(tmp_dir, out_name)
        with gzip.open(local_path, "rb") as f_in, open(out_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        if out_path.lower().endswith(".csv"):
            csv_paths.append(out_path)
    elif lower.endswith(".csv"):
        csv_paths.append(local_path)
    else:
        logger.warning("Unrecognised file extension, attempting to treat as CSV: %s", local_path)
        csv_paths.append(local_path)

    return csv_paths


def _get_csv_columns(csv_path: str) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        return next(reader, [])


def _ensure_table(conn: duckdb.DuckDBPyConnection, table: str, columns: list[str]) -> None:
    """Create table or add any missing columns."""
    # Build CREATE TABLE with all columns as VARCHAR initially; DuckDB will
    # auto-cast on COPY when types can be inferred.
    col_defs = ", ".join(f'"{c}" VARCHAR' for c in columns)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})')

    existing = {
        row[0].lower()
        for row in conn.execute(
            f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'"
        ).fetchall()
    }
    for col in columns:
        if col.lower() not in existing:
            logger.warning(
                "Schema evolution: adding column '%s' to table '%s'.", col, table
            )
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" VARCHAR')


def ingest_file(file_meta: dict, manifest: Manifest, db_path: str) -> None:
    """Ingest a single downloaded file into DuckDB.

    Args:
        file_meta: dict from manifest (must include local_path, file_id,
                   category, subcategory).
        manifest:  Manifest instance (used to mark file as ingested).
        db_path:   Path to the DuckDB database file.
    """
    local_path = file_meta.get("local_path")
    if not local_path or not os.path.exists(local_path):
        logger.error("local_path missing or file not found for file_id=%s", file_meta.get("file_id"))
        return

    table = _table_name(file_meta.get("category"), file_meta.get("subcategory"))
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            csv_paths = _extract_csv(local_path, tmp_dir)
        except Exception as exc:
            logger.error("Failed to extract %s: %s", local_path, exc)
            manifest.mark_error(file_meta["file_id"], f"extract error: {exc}")
            return

        if not csv_paths:
            logger.warning("No CSV found inside %s", local_path)
            return

        conn = duckdb.connect(db_path)
        try:
            for csv_path in csv_paths:
                columns = _get_csv_columns(csv_path)
                if not columns:
                    logger.warning("Empty CSV header in %s", csv_path)
                    continue

                _ensure_table(conn, table, columns)

                col_list = ", ".join(f'"{c}"' for c in columns)
                # Use DuckDB's read_csv_auto for type inference, append into table.
                conn.execute(
                    f"""
                    INSERT INTO "{table}" ({col_list})
                    SELECT {col_list}
                    FROM read_csv_auto('{csv_path}', header=true, ignore_errors=true)
                    """
                )
                row_count = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                logger.info(
                    "Ingested %s → table '%s' (total rows now: %s)",
                    os.path.basename(csv_path),
                    table,
                    f"{row_count:,}",
                )
        except Exception as exc:
            logger.error("Ingestion error for %s: %s", local_path, exc)
            manifest.mark_error(file_meta["file_id"], f"ingest error: {exc}")
            return
        finally:
            conn.close()

    manifest.mark_ingested(file_meta["file_id"])
