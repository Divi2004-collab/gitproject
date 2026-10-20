"""Streamlit front end for the Bank Fraud and Cheque Recovery analytics suite."""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from bank_analytics.cleaning import clean_cheques, clean_transactions
from bank_analytics.config import load_settings
from bank_analytics.drafts import cheque_reminder, internal_escalation, transaction_summary
from bank_analytics.metrics import (
    branch_recovery_summary,
    branch_risk_summary,
    cheque_metrics,
    customer_risk_summary,
    fraud_trend,
    transaction_metrics,
)
from bank_analytics.privacy import mask_frame
from bank_analytics.recovery import build_recovery_register, collection_queue, reason_summary
from bank_analytics.risk import RiskPolicy, account_risk_summary, score_transactions
from bank_analytics.validation import validate_cheques, validate_transactions


ROOT = Path(__file__).parent
SETTINGS = load_settings(ROOT)
st.set_page_config(
    page_title="Bank Risk & Recovery",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(show_spinner="Loading and preparing bank records…")
def load_data(transaction_payload: bytes | None, cheque_payload: bytes | None):
    """Load either supplied uploads or the bundled synthetic demo data."""
    if transaction_payload is None:
        transactions = pd.read_csv(SETTINGS.transactions_path)
    else:
        from io import BytesIO
        transactions = pd.read_csv(BytesIO(transaction_payload))
    if cheque_payload is None:
        cheques = pd.read_csv(SETTINGS.cheques_path)
    else:
        from io import BytesIO
        cheques = pd.read_csv(BytesIO(cheque_payload))
    tx_report = validate_transactions(transactions)
    ch_report = validate_cheques(cheques)
    tx_clean = clean_transactions(transactions)
    ch_clean = clean_cheques(cheques)
    scored = score_transactions(tx_clean, RiskPolicy(settings=SETTINGS.risk))
    register = build_recovery_register(ch_clean)
    return scored, register, tx_report, ch_report


def money(value: float) -> str:
    """Format currency values for the INR demo; adapt for multi-currency feeds."""
    return f"₹{value:,.0f}"


def mask_for_display(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the project display mask setting to sensitive identifiers."""
    if SETTINGS.pii_masking_enabled:
        return mask_frame(frame)
    return frame.copy()


def sidebar_data_controls() -> tuple[bytes | None, bytes | None, bool]:
    """Offer demo or CSV upload input, with upload sizing guidance."""
    st.sidebar.header("Data source")
    use_uploads = st.sidebar.toggle("Use uploaded CSV files", value=False)
    tx_payload = None
    ch_payload = None
    if use_uploads:
        tx_file = st.sidebar.file_uploader("Transaction CSV", type=["csv"], key="tx_upload")
        ch_file = st.sidebar.file_uploader("Cheque register CSV", type=["csv"], key="ch_upload")
        if tx_file is not None:
            if tx_file.size > SETTINGS.max_upload_mb * 1024 * 1024:
                st.sidebar.error(f"Transaction file exceeds {SETTINGS.max_upload_mb} MB.")
            else:
                tx_payload = tx_file.getvalue()
        if ch_file is not None:
            if ch_file.size > SETTINGS.max_upload_mb * 1024 * 1024:
                st.sidebar.error(f"Cheque file exceeds {SETTINGS.max_upload_mb} MB.")
            else:
                ch_payload = ch_file.getvalue()
        if tx_payload is None or ch_payload is None:
            st.sidebar.info("Upload both files. Missing upload(s) use the demo dataset.")
    else:
        st.sidebar.caption("Showing the bundled synthetic sample dataset.")
    show_identifiers = st.sidebar.toggle("Show masked identifiers", value=True)
    return tx_payload, ch_payload, show_identifiers


def filtered_views(scored: pd.DataFrame, register: pd.DataFrame):
    """Apply common branch and period filters to both dashboard datasets."""
    st.sidebar.header("Review filters")
    branches = sorted(set(scored.get("branch", pd.Series(dtype=str)).dropna())
                      | set(register.get("branch", pd.Series(dtype=str)).dropna()))
    selected_branches = st.sidebar.multiselect("Branch", branches, default=branches)
    tx_view = scored[scored["branch"].isin(selected_branches)].copy()
    ch_view = register[register["branch"].isin(selected_branches)].copy()
    if not tx_view.empty:
        valid_dates = pd.to_datetime(tx_view["transaction_ts"], errors="coerce").dropna()
        if not valid_dates.empty:
            st.sidebar.caption("Transaction date range")
            chosen = st.sidebar.date_input(
                "From / through",
                value=(valid_dates.min().date(), valid_dates.max().date()),
                min_value=valid_dates.min().date(),
                max_value=valid_dates.max().date(),
            )
            if isinstance(chosen, tuple) and len(chosen) == 2:
                start, end = chosen
                stamps = pd.to_datetime(tx_view["transaction_ts"], errors="coerce").dt.date
                tx_view = tx_view[stamps.between(start, end)]
    risk_options = ["Low", "Medium", "High", "Critical"]
    selected_risks = st.sidebar.multiselect("Risk bands", risk_options, default=risk_options)
    tx_view = tx_view[tx_view["risk_band"].isin(selected_risks)]
    return tx_view, ch_view


def show_data_quality(tx_report, ch_report) -> None:
    """Display source schema findings in an expandable review panel."""
    with st.expander("Data quality and input checks", expanded=False):
        left, right = st.columns(2)
        for column, report, title in [(left, tx_report, "Transactions"),
                                      (right, ch_report, "Cheque register")]:
            column.markdown(f"**{title}:** {report.row_count:,} input rows")
            if not report.issues:
                column.success("No structural or common-value issues detected.")
            else:
                column.dataframe(report.to_frame(), use_container_width=True, hide_index=True)
        st.caption("Validation checks do not certify source data. Review rejected or warning rows with the data owner.")


def transaction_tab(scored: pd.DataFrame, show_identifiers: bool) -> None:
    """Show risk overview, trends, account concentration, and investigator queue."""
    st.header("Transaction anomaly review")
    metrics = transaction_metrics(scored)
    flagged = scored[scored["is_flagged"]].copy()
    first = st.columns(4)
    first[0].metric("Transactions reviewed", f"{metrics['transactions_reviewed']:,}")
    first[1].metric("Flagged for review", f"{metrics['flagged_transactions']:,}",
                    f"{metrics['flagged_rate']:.1%} of filtered volume")
    first[2].metric("Flagged value", money(metrics["flagged_amount"]))
    first[3].metric("Accounts flagged", f"{metrics['flagged_accounts']:,}")

    trend_col, branch_col = st.columns(2)
    with trend_col:
        st.subheader("Flagged activity trend")
        frequency = st.selectbox("Trend interval", ["D", "W", "ME"], format_func={
            "D": "Daily", "W": "Weekly", "ME": "Monthly"
        }.get)
        trend = fraud_trend(scored, frequency=frequency)
        if trend.empty:
            st.info("No transaction dates are available for the selected filters.")
        else:
            st.line_chart(trend.set_index("period")[["flagged_count"]])
    with branch_col:
        st.subheader("Branch risk comparison")
        branch_summary = branch_risk_summary(scored)
        st.dataframe(mask_for_display(branch_summary), use_container_width=True, hide_index=True)

    account_col, customer_col = st.columns(2)
    with account_col:
        st.subheader("High-risk accounts")
        accounts = account_risk_summary(scored)
        st.dataframe(mask_for_display(accounts.head(15)), use_container_width=True, hide_index=True)
    with customer_col:
        st.subheader("Customer risk concentration")
        customers = customer_risk_summary(scored)
        st.dataframe(mask_for_display(customers.head(15)), use_container_width=True, hide_index=True)

    st.subheader("Investigator review queue")
    risk_filter, reason_filter = st.columns(2)
    with risk_filter:
        bands = sorted(flagged["risk_band"].dropna().unique())
        chosen_bands = st.multiselect("Queue risk band", bands, default=bands, key="tx_queue_band")
    with reason_filter:
        reasons = sorted({reason.strip() for cell in flagged["flag_reasons"]
                          for reason in str(cell).split(";") if reason.strip()})
        chosen_reasons = st.multiselect("Signal contains", reasons, key="tx_queue_reason")
    queue = flagged[flagged["risk_band"].isin(chosen_bands)]
    if chosen_reasons:
        queue = queue[queue["flag_reasons"].apply(lambda text: any(r in text for r in chosen_reasons))]
    queue_columns = [column for column in [
        "transaction_id", "transaction_ts", "customer_id", "account_id", "branch",
        "channel", "amount", "risk_score", "risk_band", "flag_reasons", "review_status",
    ] if column in queue]
    shown_queue = mask_for_display(queue[queue_columns]) if show_identifiers else queue[queue_columns]
    st.dataframe(shown_queue, use_container_width=True, hide_index=True)
    csv = queue.to_csv(index=False).encode("utf-8")
    st.download_button("Export review queue", csv, "transaction_review_queue.csv", "text/csv")

    if not queue.empty:
        st.subheader("Selected transaction explanation")
        selected_tx = st.selectbox("Transaction ID", queue["transaction_id"].astype(str).tolist())
        row = queue[queue["transaction_id"].astype(str).eq(selected_tx)].iloc[0]
        st.info(
            f"Risk score {row['risk_score']}/100 ({row['risk_band']}). "
            f"Signals: {row['flag_reasons']}. This is a review prioritization, not a fraud determination."
        )
        with st.expander("Auto-generated investigator summary draft"):
            draft = transaction_summary(queue)
            st.text(draft.render())


def recovery_tab(register: pd.DataFrame, show_identifiers: bool) -> None:
    """Show cheque performance, aging, collection queue, and reviewed drafts."""
    st.header("Cheque dishonour and recovery")
    metrics = cheque_metrics(register)
    cards = st.columns(4)
    cards[0].metric("Bounce rate", f"{metrics['bounce_rate']:.1%}",
                    f"{metrics['bounced_count']:,} bounced / partial")
    cards[1].metric("Outstanding recovery", money(metrics["outstanding_amount"]))
    cards[2].metric("Recovered to date", money(metrics["recovered_amount"]))
    cards[3].metric("Recovery rate", f"{metrics['recovery_rate']:.1%}")

    reason_col, aging_col, branch_col = st.columns(3)
    with reason_col:
        st.subheader("Dishonour reasons")
        reasons = reason_summary(register)
        if reasons.empty:
            st.info("No dishonour reason values in the selected data.")
        else:
            st.bar_chart(reasons.set_index("bounce_reason")[["cheque_count"]])
            st.dataframe(reasons, use_container_width=True, hide_index=True)
    with aging_col:
        st.subheader("Open amount by aging")
        age = register[register["outstanding_amount"].gt(0)].groupby(
            "aging_bucket", observed=False
        )["outstanding_amount"].sum()
        st.bar_chart(age)
        counts = register[register["outstanding_amount"].gt(0)].groupby(
            "aging_bucket", observed=False
        )["cheque_id"].count()
        st.dataframe(counts.rename("Open cheques").to_frame(), use_container_width=True)
    with branch_col:
        st.subheader("Branch recovery")
        branches = branch_recovery_summary(register)
        st.dataframe(branches, use_container_width=True, hide_index=True)

    st.subheader("Collection action queue")
    minimum_priority = st.slider("Minimum priority score", 0, 100, 0, key="queue_priority")
    queue = collection_queue(register, minimum_score=minimum_priority)
    queue_columns = [column for column in [
        "cheque_id", "cheque_no", "customer_id", "account_id", "branch", "amount",
        "recovered_amount", "outstanding_amount", "days_past_due", "aging_bucket",
        "bounce_reason", "recovery_status", "last_follow_up", "next_action",
        "priority_score", "priority_band", "owner",
    ] if column in queue]
    visible_queue = mask_for_display(queue[queue_columns]) if show_identifiers else queue[queue_columns]
    st.dataframe(visible_queue, use_container_width=True, hide_index=True)
    st.download_button(
        "Export collection queue", queue.to_csv(index=False).encode("utf-8"),
        "cheque_collection_queue.csv", "text/csv"
    )

    customers = sorted(queue["customer_id"].dropna().astype(str).unique()) if not queue.empty else []
    if customers:
        st.subheader("Customer communication drafts")
        selected_customer = st.selectbox("Customer ID", customers)
        draft_col, escalation_col = st.columns(2)
        with draft_col:
            with st.expander("Neutral payment reminder (review before use)", expanded=True):
                draft = cheque_reminder(queue, selected_customer)
                st.text(draft.render())
                st.download_button("Download reminder text", draft.render(),
                                   f"reminder_{selected_customer}.txt", "text/plain")
        with escalation_col:
            with st.expander("Internal case escalation (review before use)"):
                note = internal_escalation(queue, selected_customer)
                st.text(note.render())


def rules_tab() -> None:
    """Explain configured fraud signals and known limits of the demo rules."""
    st.header("Rules, scoring, and review guidance")
    st.markdown(
        "The dashboard combines transparent transaction signals into a bounded "
        "0–100 prioritization score. A flagged item requires analyst review; the "
        "score is not a finding of fraud. Thresholds should be calibrated against "
        "representative, labeled history before operational use."
    )
    rules = pd.DataFrame([
        {"Signal": "Unusually high amount", "Current configuration": f"Robust median/MAD and {SETTINGS.risk.quantile_fallback:.0%} quantile, with floor {money(SETTINGS.risk.minimum_amount_threshold)}", "Weight": "30"},
        {"Signal": "Unusual time", "Current configuration": f"Outside {SETTINGS.risk.odd_hour_start:02d}:00–{SETTINGS.risk.odd_hour_end:02d}:00", "Weight": "18"},
        {"Signal": "Duplicate reference", "Current configuration": "Repeated reference retained and flagged", "Weight": "40"},
        {"Signal": "Rapid transaction burst", "Current configuration": f"{SETTINGS.risk.rapid_transaction_count}+ within {SETTINGS.risk.rapid_transaction_minutes} minutes", "Weight": "22"},
        {"Signal": "First-seen payee", "Current configuration": f"First customer/payee pair above {money(SETTINGS.risk.new_payee_amount)}", "Weight": "16"},
        {"Signal": "International / channel shift", "Current configuration": "Only when source geography/history fields are supplied", "Weight": "12 each"},
    ])
    st.dataframe(rules, use_container_width=True, hide_index=True)
    st.warning("Demo policy uses synthetic data and generic thresholds. It is not a production fraud model or a legal/compliance decision engine.")
    st.subheader("Operational workflow")
    st.markdown(
        "1. Confirm the account and transaction context in the bank system.\n"
        "2. Review all active signal explanations and source-data quality warnings.\n"
        "3. Record the analyst decision, evidence reference, and disposition in the governed case system.\n"
        "4. For cheques, reconcile recovery entries and contact history before drafting a reminder.\n"
        "5. Export only fields permitted by your institution's data handling policy."
    )


def main() -> None:
    """Render navigation, common filters, data quality, and dashboard tabs."""
    st.title("Bank Fraud & Cheque Recovery")
    st.caption("Transaction anomaly review · cheque dishonour analysis · recovery tracking")
    tx_payload, ch_payload, show_identifiers = sidebar_data_controls()
    try:
        scored, register, tx_report, ch_report = load_data(tx_payload, ch_payload)
    except Exception as error:
        st.error(f"Could not load the selected datasets: {error}")
        st.info("Use the bundled sample data or check uploaded CSV column names and formats.")
        st.stop()
    show_data_quality(tx_report, ch_report)
    tx_view, ch_view = filtered_views(scored, register)
    overview, risk, recovery, guidance = st.tabs([
        "Overview", "Transaction risk", "Cheque recovery", "Rules & workflow"
    ])
    with overview:
        st.header("Portfolio snapshot")
        tx = transaction_metrics(tx_view)
        ch = cheque_metrics(ch_view)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Flagged transactions", f"{tx['flagged_transactions']:,}", f"{tx['flagged_rate']:.1%} rate")
        col2.metric("Accounts requiring review", f"{tx['flagged_accounts']:,}")
        col3.metric("Cheque bounce rate", f"{ch['bounce_rate']:.1%}")
        col4.metric("Cheque outstanding", money(ch["outstanding_amount"]))
        left, right = st.columns(2)
        with left:
            st.subheader("Fraud signal volume by branch")
            summary = branch_risk_summary(tx_view)
            if not summary.empty:
                st.bar_chart(summary.set_index("branch")[["flagged_count"]])
        with right:
            st.subheader("Cheque outstanding by branch")
            summary = branch_recovery_summary(ch_view)
            if not summary.empty:
                st.bar_chart(summary.set_index("branch")[["outstanding"]])
        show_data_quality(tx_report, ch_report)
        st.caption("All records are synthetic until you upload your own approved data extracts.")
    with risk:
        transaction_tab(tx_view, show_identifiers)
    with recovery:
        recovery_tab(ch_view, show_identifiers)
    with guidance:
        rules_tab()


if __name__ == "__main__":
    main()
