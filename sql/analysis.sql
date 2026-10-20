-- PostgreSQL starter queries. Adjust table/column names to your warehouse model.

-- Transaction cleaning: canonical timestamps, non-negative valid amounts, dedupe latest load.
WITH ranked AS (
  SELECT t.*, ROW_NUMBER() OVER (PARTITION BY reference ORDER BY transaction_ts, transaction_id) AS ref_rank
  FROM transactions t
  WHERE transaction_ts IS NOT NULL AND amount >= 0
)
SELECT * FROM ranked WHERE ref_rank = 1;

-- Customer-wise anomaly review.
SELECT customer_id, COUNT(*) AS flagged_count, SUM(amount) AS flagged_value,
       COUNT(DISTINCT account_id) AS accounts
FROM transaction_flags
WHERE is_flagged
GROUP BY customer_id ORDER BY flagged_value DESC;

-- Branch-wise fraud trend by day.
SELECT branch, DATE_TRUNC('day', transaction_ts)::date AS txn_day,
       COUNT(*) FILTER (WHERE is_flagged) AS flagged_count,
       SUM(amount) FILTER (WHERE is_flagged) AS flagged_value
FROM transaction_flags
GROUP BY branch, DATE_TRUNC('day', transaction_ts)::date
ORDER BY txn_day, branch;

-- Accounts with repeat signals in the last 30 days.
SELECT account_id, customer_id, branch, COUNT(*) AS flags, SUM(amount) AS flagged_value
FROM transaction_flags
WHERE is_flagged AND transaction_ts >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY account_id, customer_id, branch
HAVING COUNT(*) >= 2 ORDER BY flags DESC, flagged_value DESC;

-- Cheque dishonour analysis by reason.
SELECT bounce_reason, COUNT(*) AS bounced_cheques, SUM(amount) AS bounced_amount,
       SUM(recovered_amount) AS recovered_amount,
       SUM(amount - recovered_amount) AS outstanding_amount
FROM cheques WHERE bounce_date IS NOT NULL
GROUP BY bounce_reason ORDER BY bounced_cheques DESC;

-- Aging and recovery tracker. 0–30/31–60/61–90/90+ day bands.
SELECT cheque_id, customer_id, branch, amount, recovered_amount,
       GREATEST(amount - recovered_amount, 0) AS outstanding,
       GREATEST(CURRENT_DATE - due_date, 0) AS days_past_due,
       CASE WHEN CURRENT_DATE - due_date <= 30 THEN '0-30'
            WHEN CURRENT_DATE - due_date <= 60 THEN '31-60'
            WHEN CURRENT_DATE - due_date <= 90 THEN '61-90' ELSE '90+'
       END AS aging_bucket,
       status, bounce_reason, last_follow_up
FROM cheques WHERE amount > recovered_amount
ORDER BY days_past_due DESC;

-- Branch recovery performance.
SELECT branch, COUNT(*) AS cheque_count,
       COUNT(*) FILTER (WHERE bounce_date IS NOT NULL)::numeric / NULLIF(COUNT(*), 0) AS bounce_rate,
       SUM(recovered_amount) / NULLIF(SUM(amount), 0) AS recovery_ratio,
       SUM(GREATEST(amount - recovered_amount, 0)) AS outstanding
FROM cheques GROUP BY branch ORDER BY outstanding DESC;

-- Signal-level workload and value. Use the same rule_version when comparing periods.
SELECT rule_code, rule_version, analyst_status,
       COUNT(DISTINCT transaction_id) AS transactions,
       SUM(risk_points) AS signal_points,
       MIN(detected_at) AS first_seen,
       MAX(detected_at) AS last_seen
FROM fact_transaction_signal
WHERE detected_at >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY rule_code, rule_version, analyst_status
ORDER BY transactions DESC;

-- Analyst review completion and elapsed time by signal family.
SELECT rule_code,
       COUNT(*) AS total_signals,
       COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL) AS reviewed_signals,
       AVG(EXTRACT(EPOCH FROM (reviewed_at - detected_at)) / 3600.0)
           FILTER (WHERE reviewed_at IS NOT NULL) AS average_review_hours,
       COUNT(*) FILTER (WHERE disposition = 'Confirmed fraud') AS confirmed_fraud,
       COUNT(*) FILTER (WHERE disposition = 'No issue') AS cleared_signals
FROM fact_transaction_signal
GROUP BY rule_code ORDER BY total_signals DESC;

-- Rule-level precision proxy for reviewed records. Unreviewed signals are excluded.
SELECT rule_code,
       COUNT(*) FILTER (WHERE disposition = 'Confirmed fraud')::numeric
           / NULLIF(COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL), 0) AS reviewed_precision_proxy,
       COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL) AS reviewed_count
FROM fact_transaction_signal
GROUP BY rule_code
HAVING COUNT(*) FILTER (WHERE reviewed_at IS NOT NULL) > 0
ORDER BY reviewed_precision_proxy DESC;

-- Daily fraud operations dashboard: alerts raised, review queue, and closure rate.
SELECT detected_at::date AS event_day,
       COUNT(*) AS signals_raised,
       COUNT(DISTINCT transaction_id) AS transactions_flagged,
       COUNT(*) FILTER (WHERE analyst_status = 'Needs review') AS still_open,
       COUNT(*) FILTER (WHERE reviewed_at::date = detected_at::date) AS same_day_reviewed
FROM fact_transaction_signal
GROUP BY detected_at::date ORDER BY event_day;

-- Account transaction velocity: sliding 5-minute events using PostgreSQL range frames.
SELECT transaction_id, account_id, transaction_ts,
       COUNT(*) OVER (
           PARTITION BY account_id
           ORDER BY transaction_ts
           RANGE BETWEEN INTERVAL '5 minutes' PRECEDING AND CURRENT ROW
       ) AS transactions_in_5m,
       SUM(amount) OVER (
           PARTITION BY account_id
           ORDER BY transaction_ts
           RANGE BETWEEN INTERVAL '5 minutes' PRECEDING AND CURRENT ROW
       ) AS value_in_5m
FROM fact_transaction
ORDER BY account_id, transaction_ts;

-- Account day/night split to help compare individual account behavior.
SELECT account_id, customer_id,
       COUNT(*) FILTER (WHERE EXTRACT(HOUR FROM transaction_ts) BETWEEN 6 AND 21) AS daytime_count,
       COUNT(*) FILTER (WHERE EXTRACT(HOUR FROM transaction_ts) NOT BETWEEN 6 AND 21) AS off_hours_count,
       SUM(amount) FILTER (WHERE EXTRACT(HOUR FROM transaction_ts) NOT BETWEEN 6 AND 21) AS off_hours_value
FROM fact_transaction
WHERE transaction_ts >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY account_id, customer_id
ORDER BY off_hours_value DESC NULLS LAST;

-- New payee relationship history. Rank chronological payments within a customer/payee pair.
WITH payee_history AS (
    SELECT transaction_id, customer_id, counterparty, transaction_ts, amount,
           ROW_NUMBER() OVER (
               PARTITION BY customer_id, LOWER(TRIM(counterparty))
               ORDER BY transaction_ts, transaction_id
           ) AS payee_sequence
    FROM fact_transaction
    WHERE counterparty IS NOT NULL
)
SELECT *, (payee_sequence = 1) AS first_seen_payee
FROM payee_history
WHERE payee_sequence = 1
ORDER BY amount DESC;

-- Monthly customer and branch trend, including flagged value from the signal table.
WITH flagged AS (
    SELECT DISTINCT transaction_id FROM fact_transaction_signal
), monthly AS (
    SELECT DATE_TRUNC('month', t.transaction_ts)::date AS month_start,
           t.branch_code,
           COUNT(*) AS transaction_count,
           COUNT(*) FILTER (WHERE f.transaction_id IS NOT NULL) AS flagged_count,
           SUM(t.amount) FILTER (WHERE f.transaction_id IS NOT NULL) AS flagged_value
    FROM fact_transaction t
    LEFT JOIN flagged f USING (transaction_id)
    GROUP BY DATE_TRUNC('month', t.transaction_ts)::date, t.branch_code
)
SELECT *, flagged_count::numeric / NULLIF(transaction_count, 0) AS flagged_rate
FROM monthly ORDER BY month_start, branch_code;

-- Customer-level cheque exposure and latest contact.
SELECT p.customer_id,
       COUNT(*) AS cheque_count,
       SUM(p.amount) AS cheque_face_value,
       SUM(p.recovered_amount) AS recovered_amount,
       SUM(p.outstanding_amount) AS outstanding_amount,
       MAX(CURRENT_DATE - p.due_date) FILTER (WHERE p.outstanding_amount > 0) AS oldest_days_past_due,
       MAX(contact.contact_ts) AS last_contact
FROM vw_cheque_position p
LEFT JOIN vw_latest_cheque_contact contact USING (cheque_id)
GROUP BY p.customer_id
HAVING SUM(p.outstanding_amount) > 0
ORDER BY outstanding_amount DESC;

-- Recovery events by month. This preserves a movement view instead of only a balance snapshot.
SELECT DATE_TRUNC('month', recovery_date)::date AS recovery_month,
       COUNT(*) AS payment_events,
       COUNT(DISTINCT cheque_id) AS cheques_with_recovery,
       SUM(recovered_amount) AS recovered_amount
FROM fact_cheque_recovery
GROUP BY DATE_TRUNC('month', recovery_date)::date
ORDER BY recovery_month;

-- Bounce rate by reason with a consistent denominator of all cheques in each branch.
WITH reason_totals AS (
    SELECT branch_code, bounce_reason,
           COUNT(*) AS bounced_count,
           SUM(amount) AS bounced_face_value,
           SUM(outstanding_amount) AS bounced_outstanding
    FROM vw_cheque_position
    WHERE bounce_date IS NOT NULL
    GROUP BY branch_code, bounce_reason
), branch_totals AS (
    SELECT branch_code, COUNT(*) AS all_cheques
    FROM fact_cheque GROUP BY branch_code
)
SELECT r.branch_code, r.bounce_reason, r.bounced_count,
       b.all_cheques,
       r.bounced_count::numeric / NULLIF(b.all_cheques, 0) AS reason_rate,
       r.bounced_face_value, r.bounced_outstanding
FROM reason_totals r
JOIN branch_totals b USING (branch_code)
ORDER BY r.bounced_outstanding DESC;

-- Overdue follow-up queue with last contact and next scheduled action.
SELECT p.cheque_id, p.customer_id, p.branch_code, p.amount,
       p.recovered_amount, p.outstanding_amount,
       GREATEST(CURRENT_DATE - p.due_date, 0) AS days_past_due,
       contact.contact_ts AS last_contact_ts,
       contact.owner_id,
       contact.next_action_date,
       CASE
           WHEN contact.contact_ts IS NULL THEN 'No prior contact'
           WHEN CURRENT_DATE - contact.contact_ts::date > 7 THEN 'Follow-up overdue'
           ELSE 'Within follow-up SLA'
       END AS sla_state
FROM vw_cheque_position p
LEFT JOIN vw_latest_cheque_contact contact USING (cheque_id)
WHERE p.outstanding_amount > 0
ORDER BY days_past_due DESC, p.outstanding_amount DESC;

-- Aging exposure in standard 30/60/90 day intervals.
WITH open_items AS (
    SELECT *, GREATEST(CURRENT_DATE - due_date, 0) AS days_past_due
    FROM vw_cheque_position
    WHERE outstanding_amount > 0
)
SELECT CASE
           WHEN days_past_due <= 30 THEN '0-30 days'
           WHEN days_past_due <= 60 THEN '31-60 days'
           WHEN days_past_due <= 90 THEN '61-90 days'
           ELSE '90+ days'
       END AS aging_bucket,
       COUNT(*) AS open_cheques,
       SUM(outstanding_amount) AS outstanding
FROM open_items
GROUP BY 1
ORDER BY MIN(days_past_due);

-- Reconciliation check: recoveries should not exceed the cheque face value.
SELECT p.cheque_id, p.amount, p.recovered_amount,
       p.recovered_amount - p.amount AS excess_recovery
FROM vw_cheque_position p
WHERE p.recovered_amount > p.amount
ORDER BY excess_recovery DESC;

-- Duplicate source references for data quality investigation; never delete silently.
SELECT reference, COUNT(*) AS occurrences,
       ARRAY_AGG(transaction_id ORDER BY transaction_ts) AS transaction_ids,
       MIN(transaction_ts) AS first_seen,
       MAX(transaction_ts) AS latest_seen
FROM fact_transaction
WHERE reference IS NOT NULL
GROUP BY reference
HAVING COUNT(*) > 1
ORDER BY occurrences DESC;
