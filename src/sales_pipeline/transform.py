"""Reporting publication is called only inside the ingestion transaction."""


def publish(conn, run_id, head, *, snapshot=True):
    published_at = conn.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
    conn.execute("DELETE FROM reporting.open_pipeline")
    conn.execute("""INSERT INTO reporting.open_pipeline
        SELECT stages.stage, count(o.id), coalesce(sum(o.value),0)
        FROM (VALUES ('prospecting'),('qualification'),('proposal'),('negotiation')) stages(stage)
        LEFT JOIN staging.opportunities o ON o.stage=stages.stage GROUP BY stages.stage""")
    conn.execute("DELETE FROM reporting.closed_performance")
    conn.execute("""INSERT INTO reporting.closed_performance
        SELECT true, count(*) FILTER (WHERE stage='won'), count(*) FILTER (WHERE stage='lost'),
            count(*) FILTER (WHERE stage='won')::numeric / nullif(count(*),0),
            avg(extract(epoch FROM closed_at-created_at)/86400)
        FROM staging.opportunities WHERE stage IN ('won','lost')""")
    conn.execute("DELETE FROM reporting.account_activity")
    conn.execute("""INSERT INTO reporting.account_activity
        SELECT a.id, count(t.id), count(t.id) FILTER (WHERE t.kind IN ('email','meeting')),
            max(t.occurred_at), max(t.occurred_at) FILTER (WHERE t.kind IN ('email','meeting'))
        FROM staging.accounts a LEFT JOIN staging.opportunities o ON o.account_id=a.id
        LEFT JOIN staging.activities t ON t.opportunity_id=o.id GROUP BY a.id""")
    conn.execute("DELETE FROM reporting.stage_history")
    # Only explicitly supplied stage events define intervals. Missing links yield NULL durations.
    conn.execute(
        """INSERT INTO reporting.stage_history
        WITH ordered AS (
            SELECT h.*, o.stage AS current_stage,
                lead(h.changed_at) OVER w AS next_at,
                lead(h.from_stage) OVER w AS next_from,
                lag(h.to_stage) OVER w AS previous_to,
                row_number() OVER w AS position
            FROM staging.stage_changes h JOIN staging.opportunities o ON o.id=h.opportunity_id
            WINDOW w AS (PARTITION BY h.opportunity_id ORDER BY h.changed_at)
        ), intervals AS (
            SELECT *,
                ((position=1 OR from_stage=previous_to) AND
                 (next_at IS NULL OR next_from=to_stage)) IS TRUE AS valid,
                (next_at IS NULL AND to_stage=current_stage AND
                 to_stage NOT IN ('won','lost')) AS current_open
            FROM ordered
        )
        SELECT id, opportunity_id, from_stage, to_stage, changed_at, next_at, %s,
            CASE WHEN valid AND changed_at<=%s THEN
                CASE WHEN next_at IS NOT NULL AND next_at<=%s
                     THEN extract(epoch FROM next_at-changed_at)/86400
                     WHEN current_open THEN extract(epoch FROM %s-changed_at)/86400 END END,
            valid, current_open
        FROM intervals""",
        (published_at,) * 4,
    )
    if snapshot:
        # First complete observation in each UTC week; never manufacture past snapshots.
        conn.execute(
            """INSERT INTO reporting.weekly_snapshots
            SELECT date_trunc('week',%s::timestamptz AT TIME ZONE 'UTC')::date,
                   stage,%s,opportunity_count,value,%s FROM reporting.open_pipeline
            ON CONFLICT(week_start,stage) DO NOTHING""",
            (published_at, published_at, run_id),
        )
    conn.execute(
        """INSERT INTO reporting.publication
        VALUES (true,%s,%s,(SELECT max(updated_at) FROM (
            SELECT updated_at FROM staging.accounts
            UNION ALL SELECT updated_at FROM staging.opportunities
            UNION ALL SELECT updated_at FROM staging.activities
            UNION ALL SELECT updated_at FROM staging.stage_changes) versions),%s)
        ON CONFLICT(singleton) DO UPDATE SET run_id=EXCLUDED.run_id,
        published_at=EXCLUDED.published_at, source_updated_at=EXCLUDED.source_updated_at,
        source_sequence=EXCLUDED.source_sequence""",
        (run_id, published_at, head),
    )
