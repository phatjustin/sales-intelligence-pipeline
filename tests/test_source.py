import json

import pytest
from pydantic import ValidationError

from sales_pipeline.models import Opportunity
from sales_pipeline.source import Journal
from sales_pipeline.synthetic import generate


def test_reproducible_journal_and_pagination(tmp_path):
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    generate(first, 1)
    generate(second, 1)
    assert first.read_bytes() == second.read_bytes()
    journal = Journal(first)
    assert [len(page) for page in journal.pages(after=16, page_size=3)] == [3, 1]
    # A retransmitted envelope is deduplicated without dropping later arrivals.
    lines = first.read_text().splitlines()
    first.write_text("\n".join(lines[:4] + [lines[3]] + lines[4:]) + "\n")
    assert Journal(first).head == 20


@pytest.mark.parametrize("sequence", [0, True, 42])
def test_invalid_cursors_cannot_be_consumed(tmp_path, sequence):
    path = tmp_path / "crm.jsonl"
    generate(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]["sequence"] = sequence
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError):
        Journal(path)


def test_closed_timestamp_and_timezone_validation(tmp_path):
    path = tmp_path / "crm.jsonl"
    generate(path)
    record = Journal(path).events[2]["record"]
    with pytest.raises(ValidationError):
        Opportunity.model_validate({**record, "stage": "won"})
    with pytest.raises(ValidationError):
        Opportunity.model_validate({**record, "updated_at": "2026-10-01T00:00:00"})
