import pytest

from sales_pipeline.db import connect
from sales_pipeline.runner import run
from sales_pipeline.source import Journal
from sales_pipeline.synthetic import generate


@pytest.mark.integration
def test_accounts_opportunities_slice(db, tmp_path):
    path = tmp_path / "crm.jsonl"
    generate(path)
    result = run(db, Journal(path), mode="full", page_size=1)
    assert result["status"] == "succeeded"
    assert result["rows_applied"] == 16
    with connect(db) as conn:
        assert (
            conn.execute("SELECT sum(value) AS value FROM reporting.open_pipeline").fetchone()[
                "value"
            ]
            == 3000
        )
        assert conn.execute("SELECT count(*) AS n FROM raw.records").fetchone()["n"] == 16
    assert run(db, Journal(path))["rows_read"] == 0
