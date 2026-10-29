"""Per-record processing outcomes and the aggregated quality report."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Outcome(str, Enum):
    # Assigned while harvesting / normalizing
    OK = "ok"
    REPAIRED = "repaired"      # accepted, but the geometry had to be repaired
    REJECTED = "rejected"      # not loadable; always carries at least one reason
    # Assigned later by the loader (database upsert stage)
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass
class RecordResult:
    outcome: Outcome
    site_id: str | None
    source_row: int = -1
    reasons: list[str] = field(default_factory=list)    # why a record was rejected
    warnings: list[str] = field(default_factory=list)   # accepted, but with caveats
    record: dict[str, Any] | None = None                 # normalized record when accepted
    raw_properties: str | None = None                    # JSON of source attributes (rejections only)


@dataclass
class QualityReport:
    outcomes: Counter = field(default_factory=Counter)
    rejection_reasons: Counter = field(default_factory=Counter)
    warning_counts: Counter = field(default_factory=Counter)
    issues: list[dict[str, Any]] = field(default_factory=list)
    seen_site_ids: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return sum(self.outcomes[o] for o in (Outcome.OK, Outcome.REPAIRED, Outcome.REJECTED))

    def add(self, result: RecordResult) -> None:
        self.outcomes[result.outcome] += 1
        self.rejection_reasons.update(result.reasons)
        self.warning_counts.update(result.warnings)
        if result.reasons or result.warnings or result.outcome is Outcome.REPAIRED:
            self.issues.append(
                {
                    "source_row": result.source_row,
                    "site_id": result.site_id,
                    "outcome": result.outcome.value,
                    "reasons": ";".join(result.reasons),
                    "warnings": ";".join(result.warnings),
                    "raw_properties": result.raw_properties,
                }
            )

    def summary(self) -> dict[str, Any]:
        return {
            "total_processed": self.total,
            "outcomes": {o.value: n for o, n in self.outcomes.items()},
            "rejection_reasons": dict(self.rejection_reasons),
            "warnings": dict(self.warning_counts),
        }

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summary(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def write_issues_csv(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = ["source_row", "site_id", "outcome", "reasons", "warnings", "raw_properties"]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(self.issues)
        return path
