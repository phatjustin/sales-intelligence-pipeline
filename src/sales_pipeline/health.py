from sales_pipeline.db import connect


def health(url, max_age_seconds=900):
    if max_age_seconds <= 0:
        raise ValueError("positive_freshness_threshold_required")
    with connect(url) as conn:
        row = conn.execute("SELECT * FROM reporting.pipeline_health").fetchone()
    healthy = (
        row["status"] == "succeeded"
        and not row["unresolved_rejections"]
        and row["publication_age_seconds"] is not None
        and row["publication_age_seconds"] <= max_age_seconds
    )
    return dict(healthy=healthy, freshness_threshold_seconds=max_age_seconds, **row)
