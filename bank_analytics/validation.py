"""Schema validation and actionable data quality diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable
import pandas as pd


TRANSACTION_REQUIRED = {
    "transaction_id", "transaction_ts", "customer_id", "account_id",
    "branch", "txn_type", "amount", "currency", "channel", "reference",
}
CHEQUE_REQUIRED = {
    "cheque_id", "cheque_no", "issue_date", "due_date", "customer_id",
    "account_id", "branch", "amount", "status", "recovered_amount",
}


@dataclass(frozen=True)
class DataIssue:
    """One issue found during input inspection."""

    severity: str
    code: str
    column: str
    count: int
    message: str
    sample_values: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    """Summary of schema and row-level input quality checks."""

    dataset: str
    row_count: int
    issues: list[DataIssue]

    @property
    def error_count(self) -> int:
        return sum(issue.count for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(issue.count for issue in self.issues if issue.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([issue.to_dict() for issue in self.issues])


def _missing_columns(frame: pd.DataFrame, required: Iterable[str]) -> list[str]:
    return sorted(set(required) - set(frame.columns))


def _sample(series: pd.Series, limit: int = 5) -> tuple[str, ...]:
    return tuple(str(value) for value in series.dropna().unique()[:limit])


def _duplicate_issue(frame: pd.DataFrame, key: str) -> DataIssue | None:
    if key not in frame:
        return None
    duplicates = frame[key].duplicated(keep=False)
    count = int(duplicates.sum())
    if count == 0:
        return None
    return DataIssue("warning", "duplicate_key", key, count,
                     f"{count} rows share a duplicate {key}.", _sample(frame.loc[duplicates, key]))


def validate_transactions(frame: pd.DataFrame) -> ValidationReport:
    """Check the expected transaction feed columns and common bad values."""
    issues: list[DataIssue] = []
    missing = _missing_columns(frame, TRANSACTION_REQUIRED)
    if missing:
        issues.append(DataIssue("error", "missing_columns", "*", len(missing),
                                "Required fields are missing: " + ", ".join(missing)))
        return ValidationReport("transactions", len(frame), issues)
    for column in ("transaction_id", "customer_id", "account_id", "branch", "reference"):
        count = int(frame[column].isna().sum())
        if count:
            issues.append(DataIssue("error", "null_identifier", column, count,
                                    f"{count} rows have no {column} value."))
    parsed_dates = pd.to_datetime(frame["transaction_ts"], errors="coerce")
    bad_dates = int(parsed_dates.isna().sum())
    if bad_dates:
        issues.append(DataIssue("error", "invalid_timestamp", "transaction_ts", bad_dates,
                                "Timestamp is blank or cannot be parsed."))
    amounts = pd.to_numeric(frame["amount"], errors="coerce")
    bad_amounts = amounts.isna() | (amounts < 0)
    if int(bad_amounts.sum()):
        issues.append(DataIssue("error", "invalid_amount", "amount", int(bad_amounts.sum()),
                                "Amount must be numeric and non-negative.",
                                _sample(frame.loc[bad_amounts, "amount"])))
    duplicate = _duplicate_issue(frame, "transaction_id")
    if duplicate:
        issues.append(duplicate)
    return ValidationReport("transactions", len(frame), issues)


def validate_cheques(frame: pd.DataFrame) -> ValidationReport:
    """Check the cheque register schema and financial/date consistency."""
    issues: list[DataIssue] = []
    missing = _missing_columns(frame, CHEQUE_REQUIRED)
    if missing:
        issues.append(DataIssue("error", "missing_columns", "*", len(missing),
                                "Required fields are missing: " + ", ".join(missing)))
        return ValidationReport("cheques", len(frame), issues)
    for column in ("cheque_id", "customer_id", "account_id", "branch"):
        count = int(frame[column].isna().sum())
        if count:
            issues.append(DataIssue("error", "null_identifier", column, count,
                                    f"{count} rows have no {column} value."))
    for column in ("issue_date", "due_date"):
        invalid = pd.to_datetime(frame[column], errors="coerce").isna()
        if int(invalid.sum()):
            issues.append(DataIssue("error", "invalid_date", column, int(invalid.sum()),
                                    f"{column} contains a blank or invalid date."))
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    recovered = pd.to_numeric(frame["recovered_amount"], errors="coerce")
    bad = amount.isna() | recovered.isna() | (amount < 0) | (recovered < 0)
    if int(bad.sum()):
        issues.append(DataIssue("error", "invalid_money", "amount/recovered_amount",
                                int(bad.sum()), "Amounts must be numeric and non-negative."))
    over_recovered = recovered > amount
    if int(over_recovered.sum()):
        issues.append(DataIssue("warning", "recovered_exceeds_face_value", "recovered_amount",
                                int(over_recovered.sum()),
                                "Recovered amount is greater than cheque face value."))
    duplicate = _duplicate_issue(frame, "cheque_id")
    if duplicate:
        issues.append(duplicate)
    return ValidationReport("cheques", len(frame), issues)


def validation_summary(reports: Iterable[ValidationReport]) -> pd.DataFrame:
    """Turn one or more validation reports into a compact dashboard table."""
    rows = []
    for report in reports:
        rows.append({
            "dataset": report.dataset,
            "rows": report.row_count,
            "valid": report.is_valid,
            "errors": report.error_count,
            "warnings": report.warning_count,
        })
    return pd.DataFrame(rows)
