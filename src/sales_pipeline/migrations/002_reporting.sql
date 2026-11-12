CREATE TABLE staging.activities (
    id uuid PRIMARY KEY,
    opportunity_id uuid NOT NULL REFERENCES staging.opportunities(id),
    kind text NOT NULL CHECK (kind IN ('email','meeting','crm_event')),
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    fingerprint text NOT NULL,
    CHECK (occurred_at <= updated_at)
);
CREATE INDEX ON staging.activities(opportunity_id, occurred_at);
CREATE TABLE staging.stage_changes (
    id uuid PRIMARY KEY,
    opportunity_id uuid NOT NULL REFERENCES staging.opportunities(id),
    from_stage text CHECK (from_stage IN ('prospecting','qualification','proposal','negotiation','won','lost')),
    to_stage text NOT NULL CHECK (to_stage IN ('prospecting','qualification','proposal','negotiation','won','lost')),
    changed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    fingerprint text NOT NULL,
    UNIQUE(opportunity_id,changed_at),
    CHECK (from_stage IS DISTINCT FROM to_stage),
    CHECK (changed_at <= updated_at)
);
CREATE TABLE reporting.closed_performance (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    won_count bigint NOT NULL,
    lost_count bigint NOT NULL,
    win_rate numeric,
    average_sales_cycle_days numeric
);
CREATE TABLE reporting.account_activity (
    account_id uuid PRIMARY KEY,
    activity_count bigint NOT NULL,
    contact_count bigint NOT NULL,
    last_activity_at timestamptz,
    last_contact_at timestamptz
);
CREATE TABLE reporting.stage_history (
    event_id uuid PRIMARY KEY,
    opportunity_id uuid NOT NULL,
    from_stage text,
    to_stage text NOT NULL,
    entered_at timestamptz NOT NULL,
    exited_at timestamptz,
    observed_through timestamptz NOT NULL,
    duration_days numeric,
    chain_valid boolean NOT NULL,
    is_current boolean NOT NULL
);
CREATE INDEX ON reporting.stage_history(opportunity_id,entered_at);
CREATE TABLE reporting.weekly_snapshots (
    week_start date NOT NULL,
    stage text NOT NULL,
    observed_at timestamptz NOT NULL,
    opportunity_count bigint NOT NULL,
    value numeric(18,2) NOT NULL,
    run_id uuid NOT NULL REFERENCES pipeline_ops.runs(id),
    PRIMARY KEY(week_start,stage)
);

CREATE VIEW reporting.pipeline_health AS
SELECT r.id AS run_id, r.mode, r.status, r.started_at, r.finished_at,
    extract(epoch FROM coalesce(r.finished_at,clock_timestamp())-r.started_at) AS duration_seconds,
    r.rows_read, r.rows_applied, r.rejected_rows, r.attempts, r.error_code,
    r.checkpoint_before, r.checkpoint_after,
    p.published_at, p.run_id AS published_run_id, p.source_updated_at, p.source_sequence,
    extract(epoch FROM clock_timestamp()-p.published_at) AS publication_age_seconds,
    extract(epoch FROM clock_timestamp()-p.source_updated_at) AS source_event_age_seconds,
    (SELECT count(*) FROM pipeline_ops.quarantine WHERE acknowledged_at IS NULL) AS unresolved_rejections,
    (SELECT max(finished_at) FROM pipeline_ops.runs WHERE status='succeeded') AS last_success_at
FROM (SELECT 1) singleton
LEFT JOIN LATERAL (SELECT * FROM pipeline_ops.runs ORDER BY started_at DESC,id DESC LIMIT 1) r ON true
LEFT JOIN reporting.publication p ON true;
