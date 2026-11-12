"""Fixed UUIDs, dates, and amounts: all names and data are fictional."""

import json
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def uid(name):
    return str(uuid5(NAMESPACE_URL, f"synthetic-sales-pipeline/{name}"))


def generate(path, revision=0):
    events = []

    def add(entity, record):
        events.append(dict(sequence=len(events) + 1, entity=entity, record=record))

    for i in range(2):
        add(
            "accounts",
            dict(
                id=uid(f"a{i}"),
                name=f"Synthetic Account {i}",
                created_at="2026-10-01T00:00:00Z",
                updated_at="2026-10-01T00:00:00Z",
            ),
        )
    for i, stage in enumerate(["prospecting", "proposal", "won", "lost"]):
        closed = [None, None, "2026-10-11T00:00:00Z", "2026-10-21T00:00:00Z"][i]
        add(
            "opportunities",
            dict(
                id=uid(f"o{i}"),
                account_id=uid(f"a{i % 2}"),
                name=f"Synthetic Opportunity {i}",
                stage=stage,
                value=f"{(i + 1) * 1000}.00",
                created_at="2026-10-01T00:00:00Z",
                updated_at=closed or "2026-10-01T00:00:00Z",
                closed_at=closed,
            ),
        )
    for i in range(4):
        occurred = f"2026-10-0{i + 2}T00:00:00Z"
        add(
            "activities",
            dict(
                id=uid(f"t{i}"),
                opportunity_id=uid(f"o{i}"),
                kind="crm_event" if i == 3 else "email",
                occurred_at=occurred,
                created_at=occurred,
                updated_at=occurred,
            ),
        )
    for i, stage in enumerate(["prospecting", "proposal", "won", "lost"]):
        at = "2026-10-01T00:00:00Z"
        add(
            "stage_changes",
            dict(
                id=uid(f"h{i}-initial"),
                opportunity_id=uid(f"o{i}"),
                from_stage=None,
                to_stage=stage if i < 2 else "prospecting",
                changed_at=at,
                created_at=at,
                updated_at=at,
            ),
        )
        if i >= 2:
            at = ["2026-10-11T00:00:00Z", "2026-10-21T00:00:00Z"][i - 2]
            add(
                "stage_changes",
                dict(
                    id=uid(f"h{i}-closed"),
                    opportunity_id=uid(f"o{i}"),
                    from_stage="prospecting",
                    to_stage=stage,
                    changed_at=at,
                    created_at=at,
                    updated_at=at,
                ),
            )
    if revision:
        record = dict(events[2]["record"])
        record.update(stage="negotiation", value="1500.00", updated_at="2026-11-02T00:00:00Z")
        add("opportunities", record)
        add("opportunities", record)  # Deliberate content duplicate, new source delivery.
        at = "2026-10-03T12:00:00Z"
        add(
            "activities",
            dict(
                id=uid("late-activity"),
                opportunity_id=uid("o0"),
                kind="meeting",
                occurred_at=at,
                created_at=at,
                updated_at=at,
            ),
        )
        at = "2026-11-02T00:00:00Z"
        add(
            "stage_changes",
            dict(
                id=uid("h0-negotiation"),
                opportunity_id=uid("o0"),
                from_stage="prospecting",
                to_stage="negotiation",
                changed_at=at,
                created_at=at,
                updated_at=at,
            ),
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [dict(format="synthetic-crm-journal-v1", source_id="synthetic-crm-demo-v1")]
    content = "\n".join(json.dumps(row, sort_keys=True) for row in [*lines, *events]) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.replace(target)
