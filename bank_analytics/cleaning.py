"""Canonical cleaning and preparation for the two source datasets."""

from __future__ import annotations

from collections.abc import Iterable
import pandas as pd

from .validation import validate_transactions, validate_cheques, ValidationReport


TRANSACTION_TEXT = [
    "transaction_id", "customer_id", "account_id", "branch", "txn_type",
    "currency", "channel", "counterparty", "reference",
]
CHEQUE_TEXT = [
    "cheque_id", "cheque_no", "customer_id", "account_id", "branch",
    "status", "bounce_reason", "last_follow_up",
]


def normalize_column_names(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize headers to lower snake case without changing source values."""
    result = frame.copy()
    result.columns = [
        "_".join(str(column).strip().lower().replace("-", " ").split())
        for column in result.columns
    ]
    return result


def normalize_text(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Trim strings, map blank strings to missing, and standardize whitespace."""
    result = frame.copy()
    for column in columns:
        if column not in result:
            continue
        values = result[column].astype("string").str.strip()
        values = values.str.replace(r"\s+", " ", regex=True)
        result[column] = values.mask(values.eq(""))
    return result


def _parse_money(series: pd.Series) -> pd.Series:
    """Parse common CSV money formats, including commas and rupee signs."""
    cleaned = (series.astype("string").str.replace(",", "", regex=False)
               .str.replace("₹", "", regex=False).str.replace("INR", "", regex=False)
               .str.strip())
    return pd.to_numeric(cleaned, errors="coerce")


def _deduplicate(frame: pd.DataFrame, key: str, keep: str = "last") -> pd.DataFrame:
    """Drop duplicate keys deterministically after stable source ordering."""
    if key not in frame:
        return frame.copy()
    return frame.drop_duplicates(subset=[key], keep=keep).reset_index(drop=True)


def clean_transactions(frame: pd.DataFrame, keep: str = "last",
                       strict: bool = False) -> pd.DataFrame:
    """Clean, parse, validate, and deduplicate a transaction feed.

    Bad rows are retained with a `data_quality_status` field when strict=False.
    In strict mode, rows with missing identifiers, bad timestamps, or invalid
    amounts are excluded after their quality reason is recorded.
    """
    result = normalize_column_names(frame)
    result = normalize_text(result, TRANSACTION_TEXT)
    for column in ("transaction_ts", "posting_date", "value_date"):
        if column in result:
            result[column] = pd.to_datetime(result[column], errors="coerce")
    if "amount" in result:
        result["amount"] = _parse_money(result["amount"])
    if "fee_amount" in result:
        result["fee_amount"] = _parse_money(result["fee_amount"])
    quality = pd.Series("clean", index=result.index, dtype="string")
    for column in ("transaction_id", "customer_id", "account_id", "reference"):
        if column in result:
            quality = quality.mask(result[column].isna(), f"missing {column}")
    if "transaction_ts" in result:
        quality = quality.mask(result["transaction_ts"].isna(), "invalid transaction timestamp")
    if "amount" in result:
        quality = quality.mask(result["amount"].isna(), "invalid amount")
        quality = quality.mask(result["amount"] < 0, "negative amount")
    result["data_quality_status"] = quality
    result["duplicate_transaction_id"] = result.get(
        "transaction_id", pd.Series(index=result.index, dtype="string")
    ).duplicated(keep=False)
    result = _deduplicate(result, "transaction_id", keep=keep)
    if strict:
        result = result[result["data_quality_status"].eq("clean")]
    report = validate_transactions(result)
    result.attrs["validation_report"] = report
    return result.reset_index(drop=True)


def clean_cheques(frame: pd.DataFrame, keep: str = "last",
                  strict: bool = False) -> pd.DataFrame:
    """Clean the cheque ledger and add normalized status and balance fields."""
    result = normalize_column_names(frame)
    result = normalize_text(result, CHEQUE_TEXT)
    for column in ("issue_date", "due_date", "bounce_date", "last_follow_up",
                   "next_follow_up", "recovery_date"):
        if column in result:
            result[column] = pd.to_datetime(result[column], errors="coerce")
    for column in ("amount", "recovered_amount", "penalty_amount", "fees"):
        if column in result:
            result[column] = _parse_money(result[column])
    if "amount" in result:
        result["amount"] = result["amount"].fillna(0)
    if "recovered_amount" in result:
        result["recovered_amount"] = result["recovered_amount"].fillna(0)
    if "status" in result:
        result["status_normalized"] = result["status"].str.casefold().str.replace("_", " ")
    quality = pd.Series("clean", index=result.index, dtype="string")
    for column in ("cheque_id", "customer_id", "account_id", "branch"):
        if column in result:
            quality = quality.mask(result[column].isna(), f"missing {column}")
    if "due_date" in result:
        quality = quality.mask(result["due_date"].isna(), "invalid due date")
    result["data_quality_status"] = quality
    if "amount" in result and "recovered_amount" in result:
        result["outstanding_amount"] = (result["amount"] - result["recovered_amount"]).clip(lower=0)
        result["recovered_exceeds_face_value"] = result["recovered_amount"] > result["amount"]
    result = _deduplicate(result, "cheque_id", keep=keep)
    if strict:
        result = result[result["data_quality_status"].eq("clean")]
    report = validate_cheques(result)
    result.attrs["validation_report"] = report
    return result.reset_index(drop=True)


def prepare_transaction_feed(frame: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
    """Convenience wrapper returning the cleaned feed and its quality report."""
    clean = clean_transactions(frame, strict=False)
    report = clean.attrs.get("validation_report", validate_transactions(clean))
    return clean, report


def prepare_cheque_feed(frame: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
    """Convenience wrapper returning the cleaned ledger and its quality report."""
    clean = clean_cheques(frame, strict=False)
    report = clean.attrs.get("validation_report", validate_cheques(clean))
    return clean, report
