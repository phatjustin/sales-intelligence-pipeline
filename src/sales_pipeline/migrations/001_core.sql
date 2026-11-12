CREATE SCHEMA raw;
CREATE SCHEMA staging;
CREATE SCHEMA reporting;

CREATE TABLE pipeline_ops.checkpoint (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    source_id text NOT NULL,
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0)
);
CREATE TABLE pipeline_ops.runs (
    id uuid PRIMARY KEY,
    source_id text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('full','incremental','backfill')),
    status text NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    rows_read integer NOT NULL DEFAULT 0,
    rows_applied integer NOT NULL DEFAULT 0,
    rejected_rows integer NOT NULL DEFAULT 0,
    checkpoint_before bigint NOT NULL,
    checkpoint_after bigint NOT NULL,
    range_start timestamptz,
    range_end timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    error_code text
);
CREATE TABLE raw.records (
    sequence bigint PRIMARY KEY,
    entity text NOT NULL,
    payload jsonb NOT NULL,
    fingerprint text NOT NULL,
    first_run_id uuid NOT NULL REFERENCES pipeline_ops.runs(id),
    received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE pipeline_ops.quarantine (
    sequence bigint PRIMARY KEY REFERENCES raw.records(sequence),
    reason text NOT NULL,
    last_run_id uuid NOT NULL REFERENCES pipeline_ops.runs(id),
    acknowledged_at timestamptz,
    acknowledgement text
);
CREATE TABLE staging.accounts (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    fingerprint text NOT NULL
);
CREATE TABLE staging.opportunities (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES staging.accounts(id),
    name text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('prospecting','qualification','proposal','negotiation','won','lost')),
    value numeric(14,2) NOT NULL CHECK (value >= 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    closed_at timestamptz,
    fingerprint text NOT NULL,
    CHECK ((stage IN ('won','lost')) = (closed_at IS NOT NULL)),
    CHECK (closed_at IS NULL OR (closed_at >= created_at AND closed_at <= updated_at))
);
CREATE INDEX ON staging.opportunities(account_id);
CREATE TABLE reporting.publication (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    run_id uuid NOT NULL REFERENCES pipeline_ops.runs(id),
    published_at timestamptz NOT NULL,
    source_updated_at timestamptz,
    source_sequence bigint NOT NULL
);
CREATE TABLE reporting.open_pipeline (
    stage text PRIMARY KEY,
    opportunity_count bigint NOT NULL,
    value numeric(18,2) NOT NULL
);
