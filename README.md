# Bank Fraud & Cheque Recovery Dashboard

A modular bank analytics project combining transaction anomaly review with cheque dishonour tracking. It includes a Streamlit dashboard, input validation, configurable scoring, recovery prioritization, sample CSVs, SQL warehouse schema and queries, an Excel cheque register, and Power BI measures.

## Run the dashboard

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

The demo data is synthetic. Replace the CSVs under `data/` with suitably approved and minimized bank extracts. Keep account and customer identifiers masked in development, and apply your bank's access controls before using real data.

## Included

- `app.py`: dashboard navigation, upload controls, filters, visualizations, and downloadable review queues.
- `bank_analytics/`: reusable modules for configuration, cleaning, validation, anomaly scoring, recovery aging, metrics, narrative drafts, ingestion, and privacy masking.
- `config/settings.json`: editable policy defaults and relative data paths.
- `data/transactions.csv`, `data/cheques.csv`: synthetic starter data.
- `sql/schema.sql`, `sql/analysis.sql`: PostgreSQL warehouse tables, event-based recovery ledger, and analysis queries.
- `excel/cheque_register_template.xlsx`: register with aging formula, status/recovery fields, and summary sheet.
- `powerbi/measurements.dax`: starter DAX measures for a Power BI report.
- `scripts/create_cheque_template.py`: reproducible workbook builder.

## Anomaly logic

Rows can be flagged for high amount relative to the dataset (robust median/MAD threshold with quantile fallback), an unusual transaction hour, duplicate reference, rapid account velocity, first-seen payee, cross-border activity, or channel shift. Each signal is weighted into a bounded score and retained as an explanation. Missing source fields disable their dependent signal. These are review signals, not a finding of fraud. Calibrate thresholds and weights against representative, labeled history before operational use.

## GenAI report drafts

The dashboard creates a factual local summary from selected flagged rows and a neutral customer reminder or internal escalation draft for overdue dishonoured cheques. No external model call is made. Treat drafts as review-required; the system intentionally rejects automatic sending. Add an approved model/API only after your organization has approved the data handling, retention, and wording.

## Power BI

Import both CSV files and relate `transactions.customer_id` and `cheques.customer_id` to a shared customer dimension in your governed model. `powerbi/measurements.dax` contains starter measures. For date intelligence, add a marked calendar table and connect it to transaction date and cheque due date as appropriate. `sql/schema.sql` shows a normalized event-based warehouse design; use the analytical views or stage exports into Power BI.

## Project modules

| Module | Responsibility |
| --- | --- |
| `cleaning.py` | Header and text normalization, timestamp and amount parsing, duplicate handling |
| `validation.py` | Required columns, missing identifiers, invalid dates/amounts, data issue report |
| `risk.py` | Explainable signals, rolling account velocity, score bands, account summaries |
| `recovery.py` | Aging, balance reconciliation, status normalization, SLA, priority queue |
| `metrics.py` | Dashboard KPI calculations and branch/customer/time summaries |
| `drafts.py` | Factual investigator summaries and staff-reviewed reminder/escalation text |
| `ingestion.py` | CSV readers and upload/row-size limits |
| `privacy.py` | Identifier masking, keyed pseudonymization, export allow-listing |
| `config.py` | Typed policy configuration and environment path overrides |

## Input contracts

Transactions require `transaction_id`, `transaction_ts`, `customer_id`, `account_id`, `branch`, `txn_type`, `amount`, `currency`, `channel`, and `reference`. Optional fields can add signals, including `counterparty`, `customer_country`, `counterparty_country`, and prior activity history.

Cheques require `cheque_id`, `cheque_no`, `issue_date`, `due_date`, `customer_id`, `account_id`, `branch`, `amount`, `status`, and `recovered_amount`. Optional tracking fields include `bounce_date`, `bounce_reason`, `last_follow_up`, `owner`, `next_follow_up`, and `recovery_date`.

The dashboard accepts CSV uploads and checks their schemas. Missing values and invalid rows are surfaced in a quality panel; review data owners' extracts before relying on totals. The starter cleaning layer can preserve bad rows with a `data_quality_status` reason or remove them in strict mode for controlled pipelines.

## Policy configuration

Edit `config/settings.json` to adjust the time window, robust amount threshold floor, quantile fallback, rapid velocity window, risk bands, recovery aging bands, and contact SLA. `BANK_DASH_*` environment variables can override data paths and selected risk settings. Run the dashboard from the project root so relative paths stay intuitive.

## Production integration boundaries

This repository is an analytics starter, not a bank core system. It does not authenticate users, persist analyst case dispositions, connect to a payment rail, determine legal dishonour notice requirements, or send customer communications. For production, connect to governed read-only data views, store case decisions in an audited workflow system, apply role-based access and retention rules, and use approved contact templates and human approval controls.
