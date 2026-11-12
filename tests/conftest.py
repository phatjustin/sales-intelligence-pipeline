import os
from urllib.parse import urlparse

import pytest

from sales_pipeline.db import connect, migrate


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated database ending in _test")
    if not urlparse(url).path.endswith("_test"):
        pytest.fail("Tests require a dedicated database ending in _test")
    migrate(url)
    with connect(url) as conn:
        conn.execute(
            "TRUNCATE pipeline_ops.checkpoint, pipeline_ops.runs, raw.records, "
            "pipeline_ops.quarantine, staging.accounts, staging.opportunities, "
            "staging.activities, staging.stage_changes, reporting.closed_performance, "
            "reporting.account_activity, reporting.stage_history, reporting.weekly_snapshots, "
            "reporting.publication, reporting.open_pipeline CASCADE"
        )
    return url
