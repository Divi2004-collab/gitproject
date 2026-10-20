"""Explainable transaction anomaly features and risk prioritization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import math
import pandas as pd

from .config import RiskSettings


@dataclass(frozen=True)
class RiskPolicy:
    """Scoring weights and thresholds for transaction review signals."""

    settings: RiskSettings = RiskSettings()
    amount_weight: int = 30
    odd_hour_weight: int = 18
    duplicate_weight: int = 40
    rapid_velocity_weight: int = 22
    new_payee_weight: int = 16
    international_weight: int = 12
    channel_change_weight: int = 12
    blocked_channel_weight: int = 100
    blocked_channels: tuple[str, ...] = ()
    blocked_countries: tuple[str, ...] = ()

    def validate(self) -> None:
        self.settings.validate()
        values = [self.amount_weight, self.odd_hour_weight, self.duplicate_weight,
                  self.rapid_velocity_weight, self.new_payee_weight,
                  self.international_weight, self.channel_change_weight,
                  self.blocked_channel_weight]
        if any(value < 0 for value in values):
            raise ValueError("Risk signal weights cannot be negative")


SIGNAL_LABELS = {
    "odd_amount": "amount is unusually high",
    "odd_time": "transaction is outside the configured time window",
    "duplicate_reference": "reference has appeared before",
    "rapid_velocity": "many transactions occurred within a short interval",
    "new_payee": "customer has no prior history with this payee",
    "international": "transaction is cross-border",
    "channel_change": "channel differs from the customer's dominant channel",
    "blocked_channel": "channel is configured as blocked",
}


def robust_amount_threshold(amounts: pd.Series, policy: RiskPolicy) -> float:
    """Compute a robust high-amount threshold with a quantile fallback."""
    values = pd.to_numeric(amounts, errors="coerce").dropna()
    values = values[values >= 0]
    if values.empty:
        return float(policy.settings.minimum_amount_threshold)
    median = float(values.median())
    mad = float((values - median).abs().median())
    robust = median + policy.settings.robust_mad_multiplier * 1.4826 * mad
    quantile = float(values.quantile(policy.settings.quantile_fallback))
    threshold = max(robust, quantile, policy.settings.minimum_amount_threshold)
    return threshold if math.isfinite(threshold) else float("inf")


def _odd_hour(hour: int, start: int, end: int) -> bool:
    """Support ordinary and overnight allowed windows."""
    if start < end:
        return hour < start or hour >= end
    return end <= hour < start


def add_amount_signal(frame: pd.DataFrame, policy: RiskPolicy) -> pd.DataFrame:
    """Mark rows beyond the robust amount threshold and expose the threshold."""
    result = frame.copy()
    threshold = robust_amount_threshold(result.get("amount", pd.Series(dtype=float)), policy)
    amount = pd.to_numeric(result.get("amount", pd.Series(index=result.index, dtype=float)), errors="coerce")
    result["amount_threshold"] = threshold
    result["odd_amount"] = amount.ge(threshold).fillna(False)
    return result


def add_time_signal(frame: pd.DataFrame, policy: RiskPolicy) -> pd.DataFrame:
    """Create transaction hour and configured unusual-time flags."""
    result = frame.copy()
    timestamps = pd.to_datetime(result.get("transaction_ts"), errors="coerce")
    result["transaction_hour"] = timestamps.dt.hour
    settings = policy.settings
    result["odd_time"] = result["transaction_hour"].map(
        lambda hour: _odd_hour(int(hour), settings.odd_hour_start, settings.odd_hour_end)
        if pd.notna(hour) else False
    )
    return result


def add_duplicate_signal(frame: pd.DataFrame) -> pd.DataFrame:
    """Flag repeated references without dropping or hiding the source rows."""
    result = frame.copy()
    if "reference" not in result:
        result["duplicate_reference"] = False
        return result
    reference = result["reference"].astype("string").str.strip().str.casefold()
    result["duplicate_reference"] = reference.notna() & reference.duplicated(keep=False)
    return result


def add_velocity_signal(frame: pd.DataFrame, policy: RiskPolicy) -> pd.DataFrame:
    """Detect bursts per account using a rolling time-window event count."""
    result = frame.copy()
    result["rapid_velocity"] = False
    if not {"transaction_ts", "account_id"}.issubset(result.columns):
        result["transactions_in_window"] = 1
        return result
    result["transaction_ts"] = pd.to_datetime(result["transaction_ts"], errors="coerce")
    result["transactions_in_window"] = 1
    window = pd.Timedelta(minutes=policy.settings.rapid_transaction_minutes)
    for _, indices in result.dropna(subset=["transaction_ts", "account_id"]).groupby("account_id").groups.items():
        ordered = result.loc[list(indices)].sort_values("transaction_ts")
        times = ordered["transaction_ts"].tolist()
        left = 0
        counts: list[int] = []
        for right, current in enumerate(times):
            while times[left] < current - window:
                left += 1
            counts.append(right - left + 1)
        result.loc[ordered.index, "transactions_in_window"] = counts
        result.loc[ordered.index, "rapid_velocity"] = [
            count >= policy.settings.rapid_transaction_count for count in counts
        ]
    return result


def add_payee_history_signal(frame: pd.DataFrame, policy: RiskPolicy) -> pd.DataFrame:
    """Flag high-value first-seen customer/payee relationships."""
    result = frame.copy()
    result["new_payee"] = False
    if not {"customer_id", "counterparty"}.issubset(result.columns):
        return result
    counterparty = result["counterparty"].astype("string").str.strip().str.casefold()
    pairs = pd.DataFrame({"customer_id": result["customer_id"], "counterparty": counterparty}, index=result.index)
    result["new_payee"] = ~pairs.duplicated(["customer_id", "counterparty"], keep="first")
    if "amount" in result:
        result["new_payee"] &= pd.to_numeric(result["amount"], errors="coerce").ge(policy.settings.new_payee_amount)
    return result


def add_geography_signal(frame: pd.DataFrame, policy: RiskPolicy) -> pd.DataFrame:
    """Mark cross-border activity when source country fields are available."""
    result = frame.copy()
    result["international"] = False
    if {"customer_country", "counterparty_country"}.issubset(result.columns):
        origin = result["customer_country"].astype("string").str.upper()
        destination = result["counterparty_country"].astype("string").str.upper()
        result["international"] = origin.ne(destination) & origin.notna() & destination.notna()
        if policy.blocked_countries:
            blocked = {country.upper() for country in policy.blocked_countries}
            result["blocked_country"] = destination.isin(blocked)
    return result


def add_channel_signal(frame: pd.DataFrame, policy: RiskPolicy) -> pd.DataFrame:
    """Compare channel with customer history and apply optional blocked list."""
    result = frame.copy()
    result["channel_change"] = False
    if {"customer_id", "channel"}.issubset(result.columns):
        channels = result["channel"].astype("string").str.casefold()
        history = pd.DataFrame({"customer_id": result["customer_id"], "channel": channels})
        dominant = history.groupby("customer_id")["channel"].agg(
            lambda values: values.mode().iloc[0] if not values.mode().empty else pd.NA
        )
        result["dominant_channel"] = result["customer_id"].map(dominant)
        result["channel_change"] = channels.ne(result["dominant_channel"]) & result["dominant_channel"].notna()
        blocked = {channel.casefold() for channel in policy.blocked_channels}
        result["blocked_channel"] = channels.isin(blocked)
    else:
        result["blocked_channel"] = False
    return result


def signal_columns(frame: pd.DataFrame) -> list[str]:
    """Return the signal columns present on a scored transaction frame."""
    return [column for column in SIGNAL_LABELS if column in frame.columns]


def build_reason_text(row: pd.Series, columns: Iterable[str] | None = None) -> str:
    """Turn a scored row's active flags into concise audit-friendly text."""
    active = columns or SIGNAL_LABELS.keys()
    reasons = [SIGNAL_LABELS[column] for column in active
               if column in row.index and bool(row[column])]
    return "; ".join(reasons)


def risk_band(score: int, policy: RiskPolicy) -> str:
    """Map an integer score to a policy-controlled review band."""
    if score >= policy.settings.risk_critical_score:
        return "Critical"
    if score >= policy.settings.risk_high_score:
        return "High"
    if score >= policy.settings.risk_medium_score:
        return "Medium"
    return "Low"


def score_transactions(frame: pd.DataFrame,
                       policy: RiskPolicy | None = None) -> pd.DataFrame:
    """Add anomaly features, a bounded score, band, reasons, and review state.

    The score is a prioritization aid, not an assertion that fraud occurred.
    Every contributing flag and its plain-language reason is retained for
    investigator review.
    """
    policy = policy or RiskPolicy()
    policy.validate()
    result = frame.copy()
    for operation in (add_amount_signal, add_time_signal):
        result = operation(result, policy)
    result = add_duplicate_signal(result)
    result = add_velocity_signal(result, policy)
    result = add_payee_history_signal(result, policy)
    result = add_geography_signal(result, policy)
    result = add_channel_signal(result, policy)
    weights = {
        "odd_amount": policy.amount_weight,
        "odd_time": policy.odd_hour_weight,
        "duplicate_reference": policy.duplicate_weight,
        "rapid_velocity": policy.rapid_velocity_weight,
        "new_payee": policy.new_payee_weight,
        "international": policy.international_weight,
        "channel_change": policy.channel_change_weight,
        "blocked_channel": policy.blocked_channel_weight,
    }
    result["risk_score"] = 0
    for column, weight in weights.items():
        if column in result:
            result["risk_score"] += result[column].fillna(False).astype(int) * weight
    result["risk_score"] = result["risk_score"].clip(0, 100).astype(int)
    result["risk_band"] = result["risk_score"].map(lambda score: risk_band(score, policy))
    result["flag_reasons"] = result.apply(build_reason_text, axis=1)
    result["is_flagged"] = result["risk_score"] >= policy.settings.risk_medium_score
    result["review_status"] = result["is_flagged"].map({True: "Needs review", False: "Not flagged"})
    return result


def account_risk_summary(scored: pd.DataFrame) -> pd.DataFrame:
    """Summarize flagged activity and risk coverage by account."""
    required = {"account_id", "risk_score", "amount", "is_flagged"}
    if not required.issubset(scored.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(scored.columns))}")
    work = scored.copy()
    work["flagged_amount"] = work["amount"].where(work["is_flagged"], 0)
    result = work.groupby("account_id", dropna=False).agg(
        transaction_count=("risk_score", "size"),
        flagged_count=("is_flagged", "sum"),
        max_risk_score=("risk_score", "max"),
        mean_risk_score=("risk_score", "mean"),
        flagged_amount=("flagged_amount", "sum"),
    ).reset_index()
    return result.sort_values(["max_risk_score", "flagged_amount"], ascending=False)


def explain_score(row: pd.Series) -> list[dict[str, object]]:
    """Return active score contributions for a single transaction."""
    contributions = []
    for key, label in SIGNAL_LABELS.items():
        if key in row.index and bool(row[key]):
            contributions.append({"signal": key, "reason": label, "active": True})
    return contributions
