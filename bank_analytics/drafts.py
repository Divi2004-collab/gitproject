"""Review-required narrative drafts for investigators and collections teams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable
import pandas as pd


@dataclass(frozen=True)
class Draft:
    """A generated message with provenance and review metadata."""

    kind: str
    subject: str
    body: str
    generated_on: str
    source_ids: tuple[str, ...]
    disclaimer: str = "Draft only. Staff review and approval are required before use."

    def render(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}\n\n{self.disclaimer}"


def _safe_text(value: object, fallback: str = "not available") -> str:
    if value is None or pd.isna(value):
        return fallback
    return str(value).strip() or fallback


def _money(amount: float, currency: str = "INR") -> str:
    symbol = "₹" if currency.upper() == "INR" else f"{currency.upper()} "
    return f"{symbol}{amount:,.2f}"


def transaction_summary(scored: pd.DataFrame, branch: str | None = None,
                        as_of: date | None = None) -> Draft:
    """Create a factual anomaly-review memo from flagged transaction rows."""
    data = scored[scored.get("is_flagged", False).fillna(False)].copy()
    if branch:
        data = data[data["branch"].eq(branch)]
    total = float(pd.to_numeric(data.get("amount", 0), errors="coerce").fillna(0).sum())
    date_text = (as_of or date.today()).isoformat()
    lines = [f"As of {date_text}, {len(data)} transaction(s) are queued for review.",
             f"The flagged transaction value is {_money(total)}.",
             "These are rule-based signals and do not establish that fraud occurred."]
    if branch:
        lines.append(f"Branch filter: {branch}.")
    if data.empty:
        lines.append("No flagged rows matched the selected filters.")
    else:
        for row in data.sort_values("risk_score", ascending=False).head(25).itertuples():
            stamp = pd.to_datetime(getattr(row, "transaction_ts", pd.NaT), errors="coerce")
            time_text = stamp.strftime("%Y-%m-%d %H:%M") if pd.notna(stamp) else "time unavailable"
            lines.append(
                f"• {_safe_text(getattr(row, 'transaction_id', None))}: "
                f"customer {_safe_text(getattr(row, 'customer_id', None))}, "
                f"account {_safe_text(getattr(row, 'account_id', None))}, "
                f"{_money(float(getattr(row, 'amount', 0) or 0))} at {time_text}; "
                f"score {getattr(row, 'risk_score', 0)}/100 ({getattr(row, 'risk_band', 'Unknown')}); "
                f"signals: {_safe_text(getattr(row, 'flag_reasons', None), 'no explanation supplied')}."
            )
        if len(data) > 25:
            lines.append(f"Showing 25 of {len(data)} matching transactions; consult the full review export.")
    identifiers = tuple(data.get("transaction_id", pd.Series(dtype=str)).astype(str).tolist())
    return Draft("investigator_summary", "Flagged transaction review summary",
                 "\n".join(lines), date_text, identifiers)


def cheque_reminder(register: pd.DataFrame, customer_id: str,
                    as_of: date | None = None,
                    branch_contact: str = "your branch") -> Draft:
    """Draft a neutral reminder for open dishonoured cheque balances."""
    data = register[
        register["customer_id"].astype(str).eq(str(customer_id))
        & pd.to_numeric(register["outstanding_amount"], errors="coerce").gt(0)
    ].copy()
    date_text = (as_of or date.today()).isoformat()
    outstanding = float(data["outstanding_amount"].sum()) if not data.empty else 0.0
    refs = data.get("cheque_no", data.get("cheque_id", pd.Series(dtype=str))).astype(str).tolist()
    reference_text = ", ".join(refs) if refs else "the cheque account in our records"
    body = (
        f"Dear Customer,\n\nOur records as of {date_text} show an outstanding balance of "
        f"{_money(outstanding)} relating to cheque(s) {reference_text}. Please contact "
        f"{branch_contact} to confirm the balance and discuss payment or any correction "
        "at your earliest convenience. If you have already made payment, please share "
        "the details so that the records can be updated.\n\nRegards,\nBank Support Team"
    )
    return Draft("customer_reminder", "Follow-up on outstanding cheque balance",
                 body, date_text, tuple(data.get("cheque_id", pd.Series(dtype=str)).astype(str)))


def internal_escalation(register: pd.DataFrame, customer_id: str,
                        as_of: date | None = None) -> Draft:
    """Create a collections escalation note for a selected customer."""
    data = register[register["customer_id"].astype(str).eq(str(customer_id))].copy()
    data = data[data["outstanding_amount"].gt(0)]
    date_text = (as_of or date.today()).isoformat()
    oldest = int(data["days_past_due"].max()) if not data.empty else 0
    total = float(data["outstanding_amount"].sum())
    count = len(data)
    body = (
        f"Customer {customer_id} has {count} open cheque item(s) with combined "
        f"outstanding of {_money(total)}. The oldest item is {oldest} day(s) past due. "
        "Please validate prior contact history, payment allocation, and dispute status "
        "before deciding on next steps. This note is an operational prompt, not a legal notice."
    )
    return Draft("internal_escalation", "Collections case review required", body,
                 date_text, tuple(data.get("cheque_id", pd.Series(dtype=str)).astype(str)))


def draft_batch(register: pd.DataFrame, customer_ids: Iterable[str],
                as_of: date | None = None) -> list[Draft]:
    """Generate one review-required reminder per supplied customer identifier."""
    return [cheque_reminder(register, customer_id, as_of=as_of)
            for customer_id in dict.fromkeys(str(value) for value in customer_ids)]
