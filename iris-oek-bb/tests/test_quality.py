import csv
import json

from oekokonto_harvester.quality import Outcome, QualityReport, RecordResult


def test_report_counts_and_exports(tmp_path):
    report = QualityReport()
    report.add(RecordResult(Outcome.OK, "A"))
    report.add(RecordResult(Outcome.REPAIRED, "B", source_row=1))
    report.add(RecordResult(Outcome.REJECTED, None, source_row=2, reasons=["missing_site_id"], raw_properties="{}"))
    report.add(RecordResult(Outcome.OK, "C", source_row=3, warnings=["unmapped_status"]))

    summary = report.summary()
    assert summary["total_processed"] == 4
    assert summary["outcomes"] == {"ok": 2, "repaired": 1, "rejected": 1}
    assert summary["rejection_reasons"] == {"missing_site_id": 1}
    assert summary["warnings"] == {"unmapped_status": 1}

    data = json.loads(report.write_json(tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert data["total_processed"] == 4

    with open(report.write_issues_csv(tmp_path / "issues.csv"), encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["outcome"] for r in rows] == ["repaired", "rejected", "ok"]   # clean records are not listed
    assert rows[1]["reasons"] == "missing_site_id"


def test_load_outcomes_do_not_change_the_processed_total():
    report = QualityReport()
    report.add(RecordResult(Outcome.OK, "A"))
    report.outcomes.update({Outcome.INSERTED: 1})
    assert report.total == 1
