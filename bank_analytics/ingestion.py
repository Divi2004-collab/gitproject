"""CSV ingestion, format checks, and bounded uploads."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import BinaryIO
import pandas as pd


@dataclass(frozen=True)
class IngestionResult:
    """Loaded dataset metadata useful for UI confirmation and audit logs."""

    data: pd.DataFrame
    source_name: str
    source_size_bytes: int
    row_count: int
    column_count: int


def _read_csv_bytes(payload: bytes, source_name: str) -> pd.DataFrame:
    if not payload:
        raise ValueError(f"{source_name} is empty")
    try:
        return pd.read_csv(BytesIO(payload), dtype_backend="numpy_nullable")
    except TypeError:
        return pd.read_csv(BytesIO(payload))
    except UnicodeDecodeError:
        return pd.read_csv(BytesIO(payload), encoding="latin-1")


def load_csv_path(path: str | Path) -> IngestionResult:
    """Read a local CSV and attach basic source metadata."""
    source = Path(path)
    if source.suffix.casefold() not in {".csv", ".txt"}:
        raise ValueError("Only CSV or TXT input is supported")
    payload = source.read_bytes()
    data = _read_csv_bytes(payload, source.name)
    return IngestionResult(data, source.name, len(payload), len(data), len(data.columns))


def load_csv_upload(upload: BinaryIO, source_name: str,
                    max_size_mb: int = 25) -> IngestionResult:
    """Read a browser-uploaded CSV after enforcing extension and size limits."""
    if Path(source_name).suffix.casefold() != ".csv":
        raise ValueError("Upload must be a .csv file")
    payload = upload.read()
    limit = max_size_mb * 1024 * 1024
    if len(payload) > limit:
        raise ValueError(f"File exceeds the configured {max_size_mb} MB limit")
    data = _read_csv_bytes(payload, source_name)
    return IngestionResult(data, source_name, len(payload), len(data), len(data.columns))


def load_csv_text(content: str, source_name: str = "uploaded.csv") -> IngestionResult:
    """Read CSV text from a test fixture, API response, or text area."""
    if not content.strip():
        raise ValueError("CSV text is empty")
    data = pd.read_csv(StringIO(content))
    return IngestionResult(data, source_name, len(content.encode("utf-8")), len(data), len(data.columns))


def enforce_row_limit(frame: pd.DataFrame, maximum: int) -> pd.DataFrame:
    """Reject oversized feeds rather than silently truncating bank records."""
    if maximum <= 0:
        raise ValueError("maximum must be greater than zero")
    if len(frame) > maximum:
        raise ValueError(f"Input has {len(frame)} rows; maximum allowed is {maximum}")
    return frame.copy()


def require_columns(frame: pd.DataFrame, columns: set[str]) -> None:
    """Raise a clear error when an uploaded feed is missing expected fields."""
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
