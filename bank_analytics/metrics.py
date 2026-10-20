"""Reusable dashboard metrics and dimensional summaries."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import pandas as pd


@dataclass(frozen=True)
class DashboardMetrics:
    """Top-level measures shown on the fraud and recovery dashboards."""

    transactions_reviewed: int
    flagged_transactions: int
    flagged_rate: float
    flagged_amount: float
    flagged_accounts: int
    cheque_count: int
    bounced_count: int
    bounce_rate: float
    recovered_amount: float
    outstanding_amount: float
    recovery_rate: float

    def to_dict(self) -> dict:
        return asdict(self)


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Divide while keeping empty populations explicit and stable."""
    if denominator is None or pd.isna(denominator) or denominator == 0:
        return default
    return float(numerator) / float(denominator)


def transaction_metrics(scored: pd.DataFrame) -> dict[str, float | int]:
    """Calculate volume, alert, amount, and account coverage measures."""
    if scored.empty:
        return {"transactions_reviewed": 0, "flagged_transactions": 0,
                "flagged_rate": 0.0, "flagged_amount": 0.0,
                "flagged_accounts": 0, "average_flagged_amount": 0.0}
    flagged = scored[scored["is_flagged"].fillna(False)]
    return {
        "transactions_reviewed": int(len(scored)),
        "flagged_transactions": int(len(flagged)),
        "flagged_rate": safe_divide(len(flagged), len(scored)),
        "flagged_amount": float(flagged["amount"].sum()),
        "flagged_accounts": int(flagged["account_id"].nunique()) if "account_id" in flagged else 0,
        "average_flagged_amount": float(flagged["amount"].mean()) if len(flagged) else 0.0,
    }


def cheque_metrics(register: pd.DataFrame) -> dict[str, float | int]:
    """Calculate cheque bounce rate and recovery effectiveness measures."""
    if register.empty:
        return {"cheque_count": 0, "bounced_count": 0, "bounce_rate": 0.0,
                "cheque_value": 0.0, "recovered_amount": 0.0,
                "outstanding_amount": 0.0, "recovery_rate": 0.0}
    status = register.get("recovery_status", pd.Series("Pending", index=register.index))
    bounced = status.isin(["Bounced", "Partially recovered"])
    face = float(register["amount"].sum())
    recovered = float(register["recovered_amount"].sum())
    return {
        "cheque_count": int(len(register)),
        "bounced_count": int(bounced.sum()),
        "bounce_rate": safe_divide(bounced.sum(), len(register)),
        "cheque_value": face,
        "recovered_amount": recovered,
        "outstanding_amount": float(register["outstanding_amount"].sum()),
        "recovery_rate": safe_divide(recovered, face),
    }


def dashboard_metrics(scored: pd.DataFrame, register: pd.DataFrame) -> DashboardMetrics:
    """Combine the dashboard's headline measures into a typed object."""
    tx = transaction_metrics(scored)
    ch = cheque_metrics(register)
    return DashboardMetrics(
        transactions_reviewed=tx["transactions_reviewed"],
        flagged_transactions=tx["flagged_transactions"],
        flagged_rate=tx["flagged_rate"],
        flagged_amount=tx["flagged_amount"],
        flagged_accounts=tx["flagged_accounts"],
        cheque_count=ch["cheque_count"],
        bounced_count=ch["bounced_count"],
        bounce_rate=ch["bounce_rate"],
        recovered_amount=ch["recovered_amount"],
        outstanding_amount=ch["outstanding_amount"],
        recovery_rate=ch["recovery_rate"],
    )


def _date_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(pd.NaT, index=frame.index)
    return pd.to_datetime(frame[column], errors="coerce")


def fraud_trend(scored: pd.DataFrame, frequency: str = "D") -> pd.DataFrame:
    """Aggregate transaction counts and flagged value over a calendar period."""
    work = scored.copy()
    work["transaction_ts"] = _date_column(work, "transaction_ts")
    work = work.dropna(subset=["transaction_ts"])
    if work.empty:
        return pd.DataFrame(columns=["transaction_count", "flagged_count", "flagged_value"])
    work["flagged_value"] = work["amount"].where(work["is_flagged"], 0)
    grouped = work.set_index("transaction_ts").resample(frequency).agg(
        transaction_count=("transaction_id", "count"),
        flagged_count=("is_flagged", "sum"),
        flagged_value=("flagged_value", "sum"),
    )
    return grouped.reset_index(names="period")


def branch_risk_summary(scored: pd.DataFrame) -> pd.DataFrame:
    """Compare branch-level volume, flagged rate, and flagged monetary value."""
    if "branch" not in scored:
        return pd.DataFrame(columns=["branch", "transaction_count", "flagged_count", "flagged_rate", "flagged_amount"])
    work = scored.copy()
    work["flagged_amount"] = pd.to_numeric(work["amount"], errors="coerce").where(work["is_flagged"], 0)
    summary = work.groupby("branch", dropna=False).agg(
        transaction_count=("transaction_id", "count"),
        flagged_count=("is_flagged", "sum"),
        flagged_amount=("flagged_amount", "sum"),
    ).reset_index()
    summary["flagged_rate"] = summary["flagged_count"].div(summary["transaction_count"].where(summary["transaction_count"].ne(0)))
    return summary.sort_values(["flagged_amount", "flagged_count"], ascending=False)


def customer_risk_summary(scored: pd.DataFrame) -> pd.DataFrame:
    """Rank customers by signal volume, peak severity, and flagged amount."""
    flagged = scored[scored["is_flagged"].fillna(False)].copy()
    if flagged.empty:
        return pd.DataFrame(columns=["customer_id", "flagged_transactions", "flagged_amount", "max_risk_score"])
    return flagged.groupby("customer_id", dropna=False).agg(
        flagged_transactions=("transaction_id", "count"),
        flagged_amount=("amount", "sum"),
        max_risk_score=("risk_score", "max"),
    ).reset_index().sort_values(["max_risk_score", "flagged_amount"], ascending=False)


def branch_recovery_summary(register: pd.DataFrame) -> pd.DataFrame:
    """Rank branches by bounce rate and remaining recovery exposure."""
    if register.empty or "branch" not in register:
        return pd.DataFrame(columns=["branch", "cheques", "bounced", "bounce_rate", "outstanding"])
    data = register.copy()
    data["bounced_marker"] = data["recovery_status"].isin(["Bounced", "Partially recovered"])
    summary = data.groupby("branch", dropna=False).agg(
        cheques=("cheque_id", "count"),
        bounced=("bounced_marker", "sum"),
        outstanding=("outstanding_amount", "sum"),
    ).reset_index()
    summary["bounce_rate"] = summary["bounced"].div(summary["cheques"].where(summary["cheques"].ne(0)))
    return summary.sort_values(["outstanding", "bounce_rate"], ascending=False)
