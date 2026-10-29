import dataclasses
import json

import pytest
from shapely.geometry import MultiPolygon

from oekokonto_harvester.normalize import (
    clean_text, collect_eco_credits, compute_row_hash, normalize_area, normalize_status,
    parse_number, process_frame,
)
from oekokonto_harvester.quality import Outcome, QualityReport


@pytest.mark.parametrize("raw, expected", [
    ("  Bad   Belzig ", "Bad Belzig"), (None, None), ("   ", None), (12.0, "12"),
    ("Mu\u0308ncheberg", "M\u00fcncheberg"), (float("nan"), None),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("1.234,5", 1234.5), ("2,5", 2.5), ("1,234.5", 1234.5), ("1000", 1000.0), ("12 500", 12500.0),
    (12, 12.0), ("abc", None), ("", None), (None, None), (float("nan"), None), (True, None),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_status_uses_mapping_table_only(mapping):
    assert normalize_status("Umgesetzt", mapping.status_map) == "implemented"
    assert normalize_status("in Planung", mapping.status_map) == "planned"
    assert normalize_status("Sonderfall", mapping.status_map) == "unmapped"
    assert normalize_status(None, mapping.status_map) is None


def _area_props(mapping, value, unit):
    props = {mapping.fields["area_value"]: value}
    if mapping.area_unit_field:
        props[mapping.area_unit_field] = unit
    return props


@pytest.mark.parametrize("value, unit, expected_m2, warnings", [
    ("2,5", "ha", 25000.0, []),
    ("5000", "m\u00b2", 5000.0, []),
    ("1", "km2", 1_000_000.0, []),
    ("3000", None, None, ["area_unit_missing"]),
    ("10", "acre", None, ["area_unit_unknown"]),
    ("abc", "ha", None, ["unparseable_area"]),
    ("-5", "ha", None, ["negative_area"]),
])
def test_area_conversion_never_guesses(mapping, value, unit, expected_m2, warnings):
    _, _, area_m2, issues = normalize_area(_area_props(mapping, value, unit), mapping)
    assert area_m2 == expected_m2 and issues == warnings


def test_eco_credits_are_copied_not_estimated():
    assert collect_eco_credits({"a": "12 500", "b": "A-1", "c": None}, ["a", "b", "c", "d"]) == {"a": 12500.0, "b": "A-1"}
    assert collect_eco_credits({"a": None}, ["a"]) is None


def test_row_hash_detects_attribute_and_geometry_changes():
    base = compute_row_hash({"site_name": "A"}, "POLYGON EMPTY")
    assert base == compute_row_hash({"site_name": "A"}, "POLYGON EMPTY")
    assert base != compute_row_hash({"site_name": "B"}, "POLYGON EMPTY")
    assert base != compute_row_hash({"site_name": "A"}, "POLYGON ((0 0, 1 0, 1 1, 0 0))")


def test_fixture_outcome_counts(processed):
    records, report = processed
    assert report.outcomes[Outcome.OK] == 4
    assert report.outcomes[Outcome.REPAIRED] == 1
    assert report.outcomes[Outcome.REJECTED] == 5
    assert report.total == 10
    assert {r["site_id"] for r in records} == {"BB-001", "BB-002", "BB-003", "BB-009", "BB-010"}
    assert dict(report.rejection_reasons) == {
        "missing_site_id": 1, "duplicate_site_id": 1, "no_polygonal_geometry": 1,
        "missing_geometry": 1, "outside_plausible_bounds": 1}
    assert dict(report.warning_counts) == {
        "area_mismatch": 1, "area_unit_missing": 1, "unmapped_status": 1, "unparseable_area": 1}


def test_accepted_records_keep_provenance_and_projected_geometry(processed):
    records, _ = processed
    for record in records:
        assert record["state_code"] == "BB" and record["source_layer"] == "bb_flaechenpool"
        assert record["source_crs"] == "EPSG:25833" and record["source_url"].startswith("fixture://")
        assert isinstance(record["geom_25832"], MultiPolygon) and record["geom_25832"].is_valid
        assert record["source_geom_wkt"].startswith("POLYGON")
    assert next(r for r in records if r["site_id"] == "BB-003")["geometry_repaired"] is True


def test_declared_and_geometry_areas_are_kept_apart(processed):
    first = next(r for r in processed[0] if r["site_id"] == "BB-001")
    assert first["area_m2"] == 10000.0 and first["area_geom_m2"] == pytest.approx(10000, rel=0.01)
    assert first["area_geom_m2"] == pytest.approx(first["area_m2"], rel=1e-6)   # area measured in EPSG:25833


def test_error_log_payload_is_valid_json_with_original_wkt(processed):
    _, report = processed
    rejected = [i for i in report.issues if i["outcome"] == "rejected"]
    assert len(rejected) == 5
    for issue in rejected:
        assert "NaN" not in issue["raw_properties"]
        assert "_source_geom_wkt" in json.loads(issue["raw_properties"])


def test_same_input_gives_same_row_hashes(fixture_gdf, mapping, settings):
    def hashes():
        recs = process_frame(fixture_gdf, mapping, settings, "x", QualityReport(), retrieved_at="2026-01-01T00:00:00+00:00")
        return {r["site_id"]: r["row_hash"] for r in recs}
    assert hashes() == hashes()


def test_duplicates_are_caught_across_pages(fixture_gdf, mapping, settings):
    report = QualityReport()
    first = process_frame(fixture_gdf, mapping, settings, "x", report)
    before = report.rejection_reasons["duplicate_site_id"]        # the fixture's own duplicate row
    again = process_frame(fixture_gdf, mapping, settings, "x", report, row_offset=100)
    assert len(first) == 5 and again == []
    assert before == 1 and report.rejection_reasons["duplicate_site_id"] == before + 6   # 5 known sites + that row


def test_missing_crs_is_rejected_unless_fallback_is_configured(fixture_gdf, mapping, settings):
    no_crs = fixture_gdf.set_crs(None, allow_override=True)
    report = QualityReport()
    assert process_frame(no_crs, mapping, settings, "x", report) == []
    assert report.rejection_reasons["missing_crs"] >= 1
    fallback = dataclasses.replace(settings, source_crs_fallback="EPSG:25833")
    assert len(process_frame(no_crs, mapping, fallback, "x", QualityReport())) == 5
