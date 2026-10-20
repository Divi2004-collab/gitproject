"""Simple display masking and export minimization helpers."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import pandas as pd


SENSITIVE_COLUMNS = {
    "account_id", "account_number", "customer_id", "cheque_no", "customer_name", "phone", "email",
    "address", "pan", "national_id", "counterparty_account",
}


def mask_identifier(value: object, visible: int = 4, mask: str = "*") -> str:
    """Keep a short suffix for operational recognition while hiding the rest."""
    text = "" if value is None or pd.isna(value) else str(value)
    compact = "".join(character for character in text if character.isalnum())
    if len(compact) <= visible:
        return mask * max(0, len(compact) - 1) + compact[-1:]
    return mask * (len(compact) - visible) + compact[-visible:]


def mask_frame(frame: pd.DataFrame,
               columns: Iterable[str] | None = None) -> pd.DataFrame:
    """Mask recognized sensitive fields without mutating the source frame."""
    result = frame.copy()
    targets = set(columns or SENSITIVE_COLUMNS)
    for column in result.columns:
        if column.casefold() in targets:
            result[column] = result[column].map(mask_identifier)
    return result


def pseudonymize_identifier(value: object, secret: str, prefix: str = "ID") -> str:
    """Produce a stable keyed pseudonym for joining approved analysis extracts."""
    if not secret:
        raise ValueError("A non-empty secret is required for pseudonymization")
    text = "" if value is None or pd.isna(value) else str(value)
    digest = hashlib.sha256((secret + "|" + text).encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def pseudonymize_frame(frame: pd.DataFrame, columns: Iterable[str],
                       secret: str) -> pd.DataFrame:
    """Create analysis-safe identifiers while preserving consistent joins."""
    result = frame.copy()
    for column in columns:
        if column in result:
            result[column] = result[column].map(lambda value: pseudonymize_identifier(value, secret))
    return result


def minimize_export(frame: pd.DataFrame,
                    allowlist: Iterable[str]) -> pd.DataFrame:
    """Select only approved fields, rejecting misspelled allow-list entries."""
    fields = list(dict.fromkeys(allowlist))
    missing = set(fields) - set(frame.columns)
    if missing:
        raise KeyError(f"Export fields are unavailable: {sorted(missing)}")
    return frame.loc[:, fields].copy()


def sensitive_columns(frame: pd.DataFrame) -> list[str]:
    """List columns requiring display masking under the built-in policy."""
    return [column for column in frame if column.casefold() in SENSITIVE_COLUMNS]
