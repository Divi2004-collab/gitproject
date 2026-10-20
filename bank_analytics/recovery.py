"""Cheque aging, recovery status, collection queue, and SLA logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import pandas as pd

from .config import RecoverySettings


@dataclass(frozen=True)
class RecoveryPolicy:
    """Operational settings for collections prioritization."""

    settings: RecoverySettings = RecoverySettings()
    critical_outstanding: float = 100_000.0
    high_outstanding: float = 50_000.0
    escalation_after_days: int = 60
    priority_weights: tuple[int, int, int] = (35, 35, 30)

    def validate(self) -> None:
        self.settings.validate()
        if self.critical_outstanding < self.high_outstanding:
            raise ValueError("critical amount must be at least the high amount")
        if self.escalation_after_days < 0:
            raise ValueError("escalation_after_days cannot be negative")
        if sum(self.priority_weights) <= 0:
            raise ValueError("priority weights must have a positive sum")


def aging_label(days_past_due: int, bands: tuple[int, ...] = (30, 60, 90)) -> str:
    """Return a stable, user-facing aging band for a non-negative age."""
    if days_past_due <= bands[0]:
        return f"0-{bands[0]} days"
    for lower, upper in zip(bands, bands[1:]):
        if days_past_due <= upper:
            return f"{lower + 1}-{upper} days"
    return f"{bands[-1] + 1}+ days"


def _normalize_today(today: str | pd.Timestamp | None) -> pd.Timestamp:
    value = pd.Timestamp.today().normalize() if today is None else pd.Timestamp(today).normalize()
    if value.tz is not None:
        value = value.tz_localize(None)
    return value


def add_age_fields(frame: pd.DataFrame, today: str | pd.Timestamp | None = None,
                   policy: RecoveryPolicy | None = None) -> pd.DataFrame:
    """Add days past due and aging labels using an explicit as-of date."""
    policy = policy or RecoveryPolicy()
    policy.validate()
    result = frame.copy()
    as_of = _normalize_today(today)
    if "due_date" not in result:
        result["days_past_due"] = 0
    else:
        due = pd.to_datetime(result["due_date"], errors="coerce")
        result["days_past_due"] = (as_of - due.dt.tz_localize(None)).dt.days.clip(lower=0)
        result.loc[due.isna(), "days_past_due"] = pd.NA
    if "days_past_due" in result:
        result["aging_bucket"] = result["days_past_due"].map(
            lambda value: aging_label(int(value), policy.settings.aging_bands)
            if pd.notna(value) else "Date unavailable"
        )
    return result


def add_balances(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize face/recovered values and calculate gross and net exposure."""
    result = frame.copy()
    face = pd.to_numeric(result.get("amount", 0), errors="coerce")
    recovered = pd.to_numeric(result.get("recovered_amount", 0), errors="coerce")
    if not isinstance(face, pd.Series):
        face = pd.Series(face, index=result.index)
    if not isinstance(recovered, pd.Series):
        recovered = pd.Series(recovered, index=result.index)
    result["amount"] = face.fillna(0).clip(lower=0)
    result["recovered_amount"] = recovered.fillna(0).clip(lower=0)
    result["over_recovered"] = result["recovered_amount"] > result["amount"]
    result["outstanding_amount"] = (result["amount"] - result["recovered_amount"]).clip(lower=0)
    result["recovery_percentage"] = (
        result["recovered_amount"].div(result["amount"].where(result["amount"].ne(0)))
        .mul(100).fillna(0).clip(upper=100)
    )
    return result


def normalize_recovery_status(frame: pd.DataFrame,
                              settings: RecoverySettings | None = None) -> pd.DataFrame:
    """Derive a consistent recovery state from source status and amounts."""
    settings = settings or RecoverySettings()
    result = frame.copy()
    source = result.get("status", pd.Series("Pending", index=result.index)).astype("string").str.casefold()
    face = pd.to_numeric(result.get("amount", 0), errors="coerce").fillna(0)
    recovered = pd.to_numeric(result.get("recovered_amount", 0), errors="coerce").fillna(0)
    bounced = source.str.contains("bounce|dishon|return", regex=True, na=False)
    disputed = source.str.contains("disput", regex=True, na=False)
    normalized = pd.Series(settings.pending_status, index=result.index, dtype="string")
    normalized.loc[bounced] = settings.bounced_status
    normalized.loc[recovered.ge(face) & face.gt(0)] = settings.recovered_status
    normalized.loc[recovered.gt(0) & recovered.lt(face)] = settings.partial_recovery_status
    normalized.loc[disputed] = settings.disputed_status
    result["recovery_status"] = normalized
    return result


def add_follow_up_fields(frame: pd.DataFrame, today: str | pd.Timestamp | None = None,
                         policy: RecoveryPolicy | None = None) -> pd.DataFrame:
    """Mark stale or missing follow-ups and assign a next-action queue state."""
    policy = policy or RecoveryPolicy()
    result = frame.copy()
    as_of = _normalize_today(today)
    if "last_follow_up" in result:
        last = pd.to_datetime(result["last_follow_up"], errors="coerce")
        result["days_since_follow_up"] = (as_of - last.dt.tz_localize(None)).dt.days
    else:
        result["days_since_follow_up"] = pd.NA
    open_balance = pd.to_numeric(result.get("outstanding_amount", 0), errors="coerce").fillna(0).gt(0)
    age = pd.to_numeric(result.get("days_past_due", 0), errors="coerce").fillna(0)
    stale = result["days_since_follow_up"].isna() | result["days_since_follow_up"].gt(policy.settings.follow_up_sla_days)
    result["follow_up_overdue"] = open_balance & stale
    result["escalation_required"] = open_balance & age.ge(policy.escalation_after_days)
    result["next_action"] = "No action"
    result.loc[open_balance & ~stale, "next_action"] = "Monitor"
    result.loc[open_balance & stale, "next_action"] = "Contact customer"
    result.loc[result["escalation_required"], "next_action"] = "Escalate for review"
    return result


def priority_score(row: pd.Series, policy: RecoveryPolicy) -> int:
    """Prioritize by aging, exposure, and follow-up SLA using bounded points."""
    age_weight, amount_weight, sla_weight = policy.priority_weights
    days = max(0, int(row.get("days_past_due", 0) or 0))
    outstanding = max(0.0, float(row.get("outstanding_amount", 0) or 0))
    age_points = min(age_weight, int(age_weight * min(days, 120) / 120))
    amount_points = (amount_weight if outstanding >= policy.critical_outstanding else
                     int(amount_weight * outstanding / policy.critical_outstanding))
    sla_points = sla_weight if bool(row.get("follow_up_overdue", False)) else 0
    return min(100, age_points + amount_points + sla_points)


def build_recovery_register(frame: pd.DataFrame,
                            today: str | pd.Timestamp | None = None,
                            policy: RecoveryPolicy | None = None) -> pd.DataFrame:
    """Construct the complete collections register from a cleaned cheque feed."""
    policy = policy or RecoveryPolicy()
    policy.validate()
    result = add_balances(frame)
    result = add_age_fields(result, today=today, policy=policy)
    result = normalize_recovery_status(result, policy.settings)
    result = add_follow_up_fields(result, today=today, policy=policy)
    result["priority_score"] = result.apply(lambda row: priority_score(row, policy), axis=1)
    result["priority_band"] = pd.cut(
        result["priority_score"], bins=[-1, 24, 49, 74, 100],
        labels=["Low", "Medium", "High", "Critical"]
    )
    result["owner"] = result.get("owner", pd.Series(policy.settings.default_owner, index=result.index)).fillna(policy.settings.default_owner)
    return result.sort_values(["priority_score", "days_past_due"], ascending=False).reset_index(drop=True)


def recovery_summary(register: pd.DataFrame) -> pd.DataFrame:
    """Return status totals and exposure for compact charts and tables."""
    if "recovery_status" not in register:
        raise ValueError("Register must include recovery_status")
    return register.groupby("recovery_status", dropna=False).agg(
        cheque_count=("recovery_status", "size"),
        face_value=("amount", "sum"),
        recovered=("recovered_amount", "sum"),
        outstanding=("outstanding_amount", "sum"),
    ).reset_index()


def reason_summary(register: pd.DataFrame) -> pd.DataFrame:
    """Aggregate dishonour causes with recovery and outstanding values."""
    if "bounce_reason" not in register:
        raise ValueError("Register must include bounce_reason")
    data = register[register["bounce_reason"].notna()].copy()
    return data.groupby("bounce_reason", dropna=False).agg(
        cheque_count=("cheque_id", "count"),
        face_value=("amount", "sum"),
        recovered=("recovered_amount", "sum"),
        outstanding=("outstanding_amount", "sum"),
    ).reset_index().sort_values("outstanding", ascending=False)


def aging_summary(register: pd.DataFrame) -> pd.DataFrame:
    """Aggregate open balances by aging label in business order."""
    data = register[register["outstanding_amount"].gt(0)]
    result = data.groupby("aging_bucket", observed=False).agg(
        cheque_count=("cheque_id", "count"),
        outstanding=("outstanding_amount", "sum"),
    ).reset_index()
    return result


def collection_queue(register: pd.DataFrame, minimum_score: int = 0) -> pd.DataFrame:
    """Return open accounts requiring contact, ordered by operational urgency."""
    required = {"outstanding_amount", "priority_score", "next_action"}
    if not required.issubset(register.columns):
        raise ValueError(f"Missing fields: {sorted(required - set(register.columns))}")
    queue = register[register["outstanding_amount"].gt(0)
                     & register["priority_score"].ge(minimum_score)].copy()
    return queue.sort_values(["priority_score", "outstanding_amount"], ascending=False)
