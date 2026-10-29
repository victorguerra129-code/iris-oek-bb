"""Database tests: run against an ISOLATED PostgreSQL/PostGIS database (set DATABASE_URL)."""
import copy
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from oekokonto_harvester.loader import (
    MigrationError, apply_migrations, check_database, guard_sql, load_records, pending_migrations,
)
from oekokonto_harvester.quality import QualityReport

pytestmark = pytest.mark.db

CATALOG_QUERY = re.compile(r"pg_namespace|pg_extension|information_schema|to_regclass|^\s*SET LOCAL", re.I)


def _load(engine, processed, **kwargs):
    records, report = processed
    return load_records(engine, copy.deepcopy(records), report, "fixture://sample_sites.geojson", "bb_flaechenpool", **kwargs)


def _scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def test_first_load_inserts_and_logs_issues(clean_db, processed):
    result = _load(clean_db, processed)
    assert (result.inserted, result.updated, result.unchanged) == (5, 0, 0)
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_sites") == 5
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_load_issues WHERE outcome = 'rejected'") == 5
    assert _scalar(clean_db, "SELECT status FROM iris_staging.compensation_load_runs") == "completed"


def test_reloading_the_same_input_creates_no_duplicates(clean_db, processed):
    _load(clean_db, processed)
    second = _load(clean_db, processed)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 5)
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_sites") == 5
    assert _scalar(clean_db, "SELECT count(DISTINCT (state_code, site_id)) FROM iris_staging.compensation_sites") == 5


def test_changed_source_record_is_updated_not_duplicated(clean_db, processed):
    _load(clean_db, processed)
    records, report = processed
    changed = copy.deepcopy(records)
    changed[0].update(site_name="Renamed", row_hash="different")
    result = load_records(clean_db, changed, report, "x", "bb_flaechenpool")
    assert (result.inserted, result.updated, result.unchanged) == (0, 1, 4)
    assert _scalar(clean_db, f"SELECT site_name FROM iris_staging.compensation_sites WHERE site_id = '{changed[0]['site_id']}'") == "Renamed"


def test_dry_run_persists_nothing(clean_db, processed):
    result = _load(clean_db, processed, dry_run=True)
    assert result.inserted == 5 and result.dry_run
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_sites") == 0
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_load_runs") == 0


def test_failure_mid_batch_rolls_everything_back(clean_db, processed):
    records, report = processed
    broken = copy.deepcopy(records[:2])
    broken[1]["area_m2"] = -1.0                      # violates the area_m2 >= 0 constraint
    with pytest.raises(IntegrityError):
        load_records(clean_db, broken, report, "x", "bb_flaechenpool")
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_sites") == 0
    assert _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_load_runs") == 0


def test_stored_geometry_is_valid_multipolygon_in_25832(clean_db, processed):
    _load(clean_db, processed)
    with clean_db.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT ST_SRID(geom_25832), GeometryType(geom_25832), ST_IsValid(geom_25832) "
                                 "FROM iris_staging.compensation_sites")).all()
    assert rows == [(25832, "MULTIPOLYGON", True)]


def test_provenance_and_original_geometry_are_retained(clean_db, processed):
    _load(clean_db, processed)
    missing = _scalar(clean_db, "SELECT count(*) FROM iris_staging.compensation_sites WHERE source_url IS NULL "
                                "OR source_layer <> 'bb_flaechenpool' OR source_crs <> 'EPSG:25833' "
                                "OR source_geom_wkt NOT LIKE 'POLYGON%' OR state_code <> 'BB'")
    assert missing == 0


def test_every_executed_statement_targets_iris_staging_only(clean_db, processed):
    seen = []

    @event.listens_for(clean_db, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    try:
        _load(clean_db, processed)
    finally:
        event.remove(clean_db, "before_cursor_execute", capture)

    checked = [s for s in seen if not CATALOG_QUERY.search(s) and not s.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))]
    assert len(checked) >= 5
    for statement in checked:
        guard_sql(statement)                         # raises SafetyError on any other schema


def test_database_checks_pass_on_a_prepared_database(db_engine):
    assert all(passed for _, passed, _ in check_database(db_engine))


def test_migration_runner_is_idempotent_and_detects_edits(db_engine, tmp_path):
    source = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    copy_dir = tmp_path / "migrations"
    shutil.copytree(source, copy_dir)
    assert apply_migrations(db_engine, copy_dir) == []                    # nothing pending

    (copy_dir / "002_future.sql").write_text("CREATE TABLE iris_staging.never_applied (id int);\n")
    with db_engine.connect() as conn:
        assert pending_migrations(conn, copy_dir) == ["002_future"]

    first = sorted(copy_dir.glob("001_*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with db_engine.connect() as conn, pytest.raises(MigrationError, match="edited after being applied"):
        pending_migrations(conn, copy_dir)
