"""An append-only source journal with an arrival-order cursor, independent of event time."""

import hashlib
import json
from pathlib import Path

from sales_pipeline.models import timestamp


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Journal:
    def __init__(self, path):
        # Freeze the source for this run: subsequent file changes belong to the next run.
        lines = Path(path).read_text().splitlines()
        manifest = json.loads(lines[0])
        if manifest.get("format") != "synthetic-crm-journal-v1":
            raise ValueError("unsupported_source_format")
        self.source_id = manifest["source_id"]
        if not isinstance(self.source_id, str) or not 1 <= len(self.source_id) <= 100:
            raise ValueError("invalid_source_identity")
        self.events = []
        seen = {}
        previous = 0
        for line in lines[1:]:
            event = json.loads(line)
            seq = event["sequence"]
            if type(seq) is not int or seq <= 0:
                raise ValueError("invalid_source_sequence")
            digest = fingerprint(event)
            if seq in seen:
                if seen[seq] != digest:
                    raise ValueError("source_sequence_reused")
                continue
            if seq != previous + 1:
                raise ValueError("source_sequence_gap_or_reorder")
            if not isinstance(event.get("record"), dict) or not isinstance(
                event.get("entity"), str
            ):
                raise ValueError("invalid_source_envelope")
            seen[seq] = digest
            previous = seq
            self.events.append(event)
        self.head = previous

    def pages(self, *, after=0, start=None, end=None, page_size=100):
        selected = []
        for event in self.events:
            if event["sequence"] <= after:
                continue
            if start is not None:
                try:
                    updated = timestamp(event["record"]["updated_at"])
                except (ValueError, KeyError, TypeError):
                    updated = None  # Still send malformed rows to quarantine.
                if updated is not None and not start <= updated < end:
                    continue
            selected.append(event)
        for offset in range(0, len(selected), page_size):
            yield selected[offset : offset + page_size]
