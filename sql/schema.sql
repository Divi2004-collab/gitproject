-- PostgreSQL reference schema for a governed analytics warehouse.
-- Replace sample account identifiers with the institution's tokenization scheme.

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id VARCHAR(80) NOT NULL UNIQUE,
    segment VARCHAR(40),
    home_branch_code VARCHAR(20),
    customer_country CHAR(2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS dim_account (
    account_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id VARCHAR(100) NOT NULL UNIQUE,
    customer_id VARCHAR(80) NOT NULL REFERENCES dim_customer(customer_id),
    branch_code VARCHAR(20) NOT NULL,
    account_type VARCHAR(40),
    currency CHAR(3) NOT NULL DEFAULT 'INR',
    opened_on DATE,
    closed_on DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on >= opened_on)
);

CREATE TABLE IF NOT EXISTS dim_branch (
    branch_code VARCHAR(20) PRIMARY KEY,
    branch_name VARCHAR(120) NOT NULL,
    region VARCHAR(80),
    state_name VARCHAR(80),
    city VARCHAR(80),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS fact_transaction (
    transaction_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_id VARCHAR(120) NOT NULL UNIQUE,
    transaction_ts TIMESTAMPTZ NOT NULL,
    customer_id VARCHAR(80) NOT NULL REFERENCES dim_customer(customer_id),
    account_id VARCHAR(100) NOT NULL REFERENCES dim_account(account_id),
    branch_code VARCHAR(20) NOT NULL REFERENCES dim_branch(branch_code),
    txn_type VARCHAR(30) NOT NULL,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount >= 0),
    currency CHAR(3) NOT NULL,
    channel VARCHAR(40),
    counterparty VARCHAR(160),
    reference VARCHAR(160),
    counterparty_country CHAR(2),
    source_batch_id VARCHAR(100) NOT NULL,
    source_row_number INTEGER,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_batch_id, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_transaction_account_time
    ON fact_transaction (account_id, transaction_ts DESC);
CREATE INDEX IF NOT EXISTS ix_transaction_customer_time
    ON fact_transaction (customer_id, transaction_ts DESC);
CREATE INDEX IF NOT EXISTS ix_transaction_branch_time
    ON fact_transaction (branch_code, transaction_ts DESC);
CREATE INDEX IF NOT EXISTS ix_transaction_reference
    ON fact_transaction (reference);

CREATE TABLE IF NOT EXISTS fact_transaction_signal (
    signal_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_id VARCHAR(120) NOT NULL REFERENCES fact_transaction(transaction_id),
    rule_code VARCHAR(60) NOT NULL,
    rule_version VARCHAR(40) NOT NULL,
    risk_points SMALLINT NOT NULL CHECK (risk_points BETWEEN 0 AND 100),
    signal_description VARCHAR(500) NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    analyst_status VARCHAR(30) NOT NULL DEFAULT 'Needs review',
    analyst_id VARCHAR(100),
    disposition VARCHAR(60),
    disposition_note TEXT,
    reviewed_at TIMESTAMPTZ,
    UNIQUE (transaction_id, rule_code, rule_version)
);

CREATE INDEX IF NOT EXISTS ix_signal_status_detected
    ON fact_transaction_signal (analyst_status, detected_at DESC);

CREATE TABLE IF NOT EXISTS fact_cheque (
    cheque_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cheque_id VARCHAR(120) NOT NULL UNIQUE,
    cheque_no VARCHAR(80) NOT NULL,
    issue_date DATE,
    due_date DATE NOT NULL,
    customer_id VARCHAR(80) NOT NULL REFERENCES dim_customer(customer_id),
    account_id VARCHAR(100) NOT NULL REFERENCES dim_account(account_id),
    branch_code VARCHAR(20) NOT NULL REFERENCES dim_branch(branch_code),
    amount NUMERIC(18, 2) NOT NULL CHECK (amount >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'INR',
    status VARCHAR(40) NOT NULL,
    bounce_date DATE,
    bounce_reason VARCHAR(120),
    source_batch_id VARCHAR(100) NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (bounce_date IS NULL OR bounce_date >= due_date - INTERVAL '365 days')
);

CREATE INDEX IF NOT EXISTS ix_cheque_customer_due
    ON fact_cheque (customer_id, due_date DESC);
CREATE INDEX IF NOT EXISTS ix_cheque_branch_status
    ON fact_cheque (branch_code, status);

CREATE TABLE IF NOT EXISTS fact_cheque_recovery (
    recovery_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cheque_id VARCHAR(120) NOT NULL REFERENCES fact_cheque(cheque_id),
    recovery_date DATE NOT NULL,
    recovered_amount NUMERIC(18, 2) NOT NULL CHECK (recovered_amount > 0),
    payment_reference VARCHAR(160),
    recorded_by VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_recovery_cheque_date
    ON fact_cheque_recovery (cheque_id, recovery_date);

CREATE TABLE IF NOT EXISTS fact_collection_contact (
    contact_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cheque_id VARCHAR(120) NOT NULL REFERENCES fact_cheque(cheque_id),
    contact_ts TIMESTAMPTZ NOT NULL,
    contact_channel VARCHAR(40) NOT NULL,
    contact_outcome VARCHAR(80),
    owner_id VARCHAR(100),
    next_action_date DATE,
    approved_template_id VARCHAR(80),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Latest recovery position per cheque; do not overwrite the underlying events.
CREATE OR REPLACE VIEW vw_cheque_position AS
SELECT c.cheque_id,
       c.cheque_no,
       c.customer_id,
       c.account_id,
       c.branch_code,
       c.due_date,
       c.amount,
       COALESCE(SUM(r.recovered_amount), 0)::NUMERIC(18, 2) AS recovered_amount,
       GREATEST(c.amount - COALESCE(SUM(r.recovered_amount), 0), 0)::NUMERIC(18, 2) AS outstanding_amount,
       c.status,
       c.bounce_date,
       c.bounce_reason
FROM fact_cheque c
LEFT JOIN fact_cheque_recovery r ON r.cheque_id = c.cheque_id
GROUP BY c.cheque_id, c.cheque_no, c.customer_id, c.account_id,
         c.branch_code, c.due_date, c.amount, c.status,
         c.bounce_date, c.bounce_reason;

-- Latest customer contact supports SLA calculations without losing history.
CREATE OR REPLACE VIEW vw_latest_cheque_contact AS
SELECT DISTINCT ON (cheque_id)
       cheque_id, contact_ts, contact_channel, contact_outcome,
       owner_id, next_action_date
FROM fact_collection_contact
ORDER BY cheque_id, contact_ts DESC, contact_key DESC;
