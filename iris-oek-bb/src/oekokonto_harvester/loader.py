"""Transactional, idempotent load of normalized records into iris_staging only.

Safety model (documented in the README):
  1. Every statement executed here passes `guard_sql`, which rejects any write or
     DDL aimed at a schema other than iris_staging (and any DROP/GRANT/EXTENSION/...).
  2. The search_path is pinned to iris_staging for each transaction.
  3. A pre-flight check refuses to run if the connected role could write to iris_core.
  4. The recommended database role (sql/roles.sql) has no rights outside iris_staging.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import shapely
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url

from .config import resolve_path
from .quality import Outcome, QualityReport

log = logging.getLogger(__name__)

SCHEMA = "iris_staging"            # the only schema this module ever writes to
SITES = f"{SCHEMA}.compensation_sites"
RUNS = f"{SCHEMA}.compensation_load_runs"
ISSUES = f"{SCHEMA}.compensation_load_issues"
MIGRATIONS = f"{SCHEMA}.schema_migrations"
PIN_SEARCH_PATH = f"SET LOCAL search_path TO {SCHEMA}, public"   # public: PostGIS types/functions
BATCH_SIZE = 500


class SafetyError(RuntimeError):
    """A statement or target violates the iris_staging-only policy."""


class SchemaMismatchError(RuntimeError):
    """The database structure does not match what the loader writes."""


class MigrationError(RuntimeError):
    """Migrations are pending, missing prerequisites, or were edited after being applied."""


# ---------------------------------------------------------------- SQL guard ----
_LITERALS = re.compile(r"'(?:[^']|'')*'")
_COMMENTS = re.compile(r"--[^\n]*")
_ALLOWED_START = re.compile(
    r"^\s*(?:INSERT\s+INTO|UPDATE|SELECT|WITH|CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)|ALTER\s+TABLE)\b", re.I
)
_DENIED = re.compile(
    r"\b(?:CREATE|DROP|ALTER)\s+(?:EXTENSION|SCHEMA|DATABASE|ROLE|USER)\b"
    r"|\b(?:GRANT|REVOKE|COPY|CALL)\b|\bSET\s+(?:LOCAL\s+)?search_path\b|\bDO\s+\$",
    re.I,
)
_TARGETS = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM|JOIN|TRUNCATE(?:\s+TABLE)?|REFERENCES"
    r"|(?:CREATE|ALTER|DROP)\s+TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?(?:\s+ONLY)?"
    r"|CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+[\w$]+\s+ON(?:\s+ONLY)?)"
    r"\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
    re.I,
)


def guard_sql(sql: str) -> str:
    """Return `sql` unchanged if it only touches iris_staging; raise SafetyError otherwise."""
    cleaned = _COMMENTS.sub("", _LITERALS.sub("''", sql))
    if '"' in cleaned:
        raise SafetyError("Quoted identifiers are not allowed in loader statements.")
    cleaned = re.sub(r"\bDO\s+UPDATE\b", "DO_UPDATE", cleaned, flags=re.I)   # ON CONFLICT ... DO UPDATE
    if not _ALLOWED_START.match(cleaned) or _DENIED.search(cleaned):
        raise SafetyError(f"Statement type not permitted: {sql.strip()[:80]!r}")
    for match in _TARGETS.finditer(cleaned):
        if not match.group(1).lower().startswith(f"{SCHEMA}."):
            raise SafetyError(f"Statement targets '{match.group(1)}', outside {SCHEMA}: {sql.strip()[:80]!r}")
    return sql


def _g(sql: str):
    """Guarded SQLAlchemy text()."""
    return text(guard_sql(sql))


# ----------------------------------------------------------------- statements ----
VALUE_COLUMNS = [
    "site_name", "municipality", "cadastral_district", "compensation_status_raw",
    "compensation_status", "area_source_value", "area_source_unit", "area_m2",
    "area_geom_m2", "eco_credit_fields", "geom_25832", "source_crs", "source_geom_wkt",
    "geometry_repaired", "source_url", "source_layer", "retrieved_at", "row_hash",
]
EXPECTED_COLUMNS = {"state_code", "site_id", *VALUE_COLUMNS, "last_run_id"}
_EXPRESSIONS = {
    "eco_credit_fields": "CAST(:eco_credit_fields AS jsonb)",
    "geom_25832": "ST_SetSRID(ST_GeomFromWKB(:geom_wkb), 25832)",
    "retrieved_at": "CAST(:retrieved_at AS timestamptz)",
}


def _build_upsert_sql() -> str:
    columns = ["state_code", "site_id", *VALUE_COLUMNS, "last_run_id"]
    placeholders = [":state_code", ":site_id"]
    placeholders += [_EXPRESSIONS.get(c, f":{c}") for c in VALUE_COLUMNS]
    placeholders.append("CAST(:run_id AS uuid)")
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in VALUE_COLUMNS)
    return (
        f"INSERT INTO {SITES} ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (state_code, site_id) DO UPDATE SET {updates}, "
        f"last_run_id = EXCLUDED.last_run_id, updated_at = now()"
    )


UPSERT_SQL = _build_upsert_sql()   # guarded when executed
INSERT_RUN_SQL = (f"INSERT INTO {RUNS} (run_id, source_url, source_layer, dry_run) "
                  "VALUES (CAST(:run_id AS uuid), :source_url, :source_layer, :dry_run)")
INSERT_ISSUE_SQL = (f"INSERT INTO {ISSUES} (run_id, source_row, site_id, outcome, reasons, warnings, raw_properties) "
                    "VALUES (CAST(:run_id AS uuid), :source_row, :site_id, :outcome, :reasons, :warnings, "
                    "CAST(:raw_properties AS jsonb))")
FINISH_RUN_SQL = (f"UPDATE {RUNS} SET finished_at = now(), status = :status, counts = CAST(:counts AS jsonb) "
                  "WHERE run_id = CAST(:run_id AS uuid)")
SELECT_HASHES_SQL = f"SELECT site_id, row_hash FROM {SITES} WHERE state_code = :state_code AND site_id = ANY(:ids)"


@dataclass
class LoadResult:
    run_id: str
    inserted: int
    updated: int
    unchanged: int
    dry_run: bool


# ------------------------------------------------------------------ connection ----
def get_engine(url: str | None = None) -> Engine:
    """Create an engine from DATABASE_URL (never stored in files) with a name check."""
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: postgresql+psycopg://user:password@localhost:5432/dbname"
        )
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    database = (parsed.database or "").lower()
    if database == "iris_core" or "prod" in database:
        raise SafetyError(f"Refusing to connect to '{parsed.database}': only isolated staging resources are allowed.")
    return create_engine(parsed)


def verify_target(conn: Connection) -> None:
    """Pre-flight: iris_staging must exist and the role must not be able to write to iris_core."""
    schemas = {row[0] for row in conn.execute(
        text("SELECT nspname FROM pg_namespace WHERE nspname IN ('iris_staging', 'iris_core')"))}
    if SCHEMA not in schemas:
        raise MigrationError("Schema iris_staging does not exist. An administrator must run sql/00_prerequisites.sql.")
    if "iris_core" in schemas:
        # OIDs (not names) so the check also works for roles without USAGE on iris_core.
        can_write = conn.execute(text("""
            SELECT has_schema_privilege(current_user, 'iris_core', 'CREATE')
                OR EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'iris_core' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND (has_table_privilege(current_user, c.oid, 'INSERT')
                        OR has_table_privilege(current_user, c.oid, 'UPDATE')
                        OR has_table_privilege(current_user, c.oid, 'DELETE'))
                )""")).scalar()
        if can_write:
            raise SafetyError("The connected role can write to iris_core. Use the least-privilege role in sql/roles.sql.")


# ------------------------------------------------------------------ migrations ----
def _split_sql(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _migration_files(directory: str | Path) -> list[Path]:
    return sorted(resolve_path(directory).glob("*.sql"))


def _applied(conn: Connection) -> dict[str, str]:
    if not conn.execute(text(f"SELECT to_regclass('{MIGRATIONS}') IS NOT NULL")).scalar():
        return {}
    return {version: checksum for version, checksum in conn.execute(_g(f"SELECT version, checksum FROM {MIGRATIONS}"))}


def pending_migrations(conn: Connection, directory: str | Path = "sql/migrations") -> list[str]:
    applied = _applied(conn)
    pending = []
    for path in _migration_files(directory):
        if path.stem not in applied:
            pending.append(path.stem)
        elif applied[path.stem] != _checksum(path):
            raise MigrationError(f"Migration {path.stem} was edited after being applied. Add a new migration instead.")
    return pending


def apply_migrations(engine: Engine, directory: str | Path = "sql/migrations") -> list[str]:
    """Apply pending migrations in ONE transaction; returns the versions applied now."""
    done: list[str] = []
    with engine.begin() as conn:
        conn.exec_driver_sql(PIN_SEARCH_PATH)
        verify_target(conn)
        if not conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")).scalar():
            raise MigrationError("PostGIS is not installed in this database. Run sql/00_prerequisites.sql as an administrator.")
        conn.execute(_g(f"CREATE TABLE IF NOT EXISTS {MIGRATIONS} (version text PRIMARY KEY, "
                        "checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"))
        applied = _applied(conn)
        for path in _migration_files(directory):
            checksum = _checksum(path)
            if path.stem in applied:
                if applied[path.stem] != checksum:
                    raise MigrationError(f"Migration {path.stem} was edited after being applied. Add a new migration instead.")
                continue
            for statement in _split_sql(path.read_text(encoding="utf-8")):
                conn.exec_driver_sql(guard_sql(statement))
            conn.execute(_g(f"INSERT INTO {MIGRATIONS} (version, checksum) VALUES (:version, :checksum)"),
                         {"version": path.stem, "checksum": checksum})
            done.append(path.stem)
    return done


def check_structure(conn: Connection) -> None:
    actual = {row[0] for row in conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'iris_staging' AND table_name = 'compensation_sites'"))}
    if not actual:
        raise SchemaMismatchError("Table iris_staging.compensation_sites not found. Run: init-db")
    missing = EXPECTED_COLUMNS - actual
    if missing:
        raise SchemaMismatchError(f"compensation_sites is missing columns the loader writes: {sorted(missing)}")
    extra = actual - EXPECTED_COLUMNS - {"first_loaded_at", "updated_at"}
    if extra:
        log.warning("compensation_sites has extra columns the loader ignores: %s", sorted(extra))


def _require_no_pending(conn: Connection) -> None:
    pending = pending_migrations(conn)
    if pending:
        raise MigrationError(f"Pending migrations: {pending}. Run: init-db")


def check_database(engine: Engine) -> list[tuple[str, bool, str]]:
    """Run the pre-load checks without writing anything; returns (check, passed, detail)."""
    checks: list[tuple[str, Callable[[Connection], None]]] = [
        ("target is isolated staging", verify_target),
        ("migrations applied", _require_no_pending),
        ("table structure matches loader", check_structure),
    ]
    results = []
    with engine.connect() as conn:
        conn.exec_driver_sql(PIN_SEARCH_PATH)
        for name, check in checks:
            try:
                check(conn)
                results.append((name, True, "ok"))
            except Exception as exc:  # report every failing check, not just the first
                conn.rollback()
                conn.exec_driver_sql(PIN_SEARCH_PATH)
                results.append((name, False, str(exc)))
    return results


# ------------------------------------------------------------------------ load ----
def _params(record: dict[str, Any], run_id: str) -> dict[str, Any]:
    params = {key: record.get(key) for key in ("state_code", "site_id", *VALUE_COLUMNS)}
    eco = record.get("eco_credit_fields")
    params["eco_credit_fields"] = json.dumps(eco, ensure_ascii=False) if eco is not None else None
    params["geom_wkb"] = shapely.to_wkb(record["geom_25832"])
    params["run_id"] = run_id
    params.pop("geom_25832", None)
    return params


def _batches(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _existing_hashes(conn: Connection, state_code: str, site_ids: list[str]) -> dict[str, str]:
    rows = conn.execute(_g(SELECT_HASHES_SQL), {"state_code": state_code, "ids": site_ids})
    return {site_id: row_hash for site_id, row_hash in rows}


def load_records(
    engine: Engine,
    records: list[dict[str, Any]],
    report: QualityReport,
    source_url: str,
    source_layer: str,
    dry_run: bool = False,
) -> LoadResult:
    """Upsert all records plus run metadata and issues in ONE transaction.

    'unchanged' = same row_hash already stored; 'updated' = key exists with a
    different hash; otherwise 'inserted'. Any error rolls everything back;
    dry_run=True rolls back on purpose.
    """
    keys = [(r["state_code"], r["site_id"]) for r in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate (state_code, site_id) keys in the batch; they must be rejected earlier")

    run_id = str(uuid.uuid4())
    counts: Counter = Counter()
    conn = engine.connect()
    transaction = conn.begin()
    try:
        conn.exec_driver_sql(PIN_SEARCH_PATH)
        verify_target(conn)
        _require_no_pending(conn)
        check_structure(conn)

        conn.execute(_g(INSERT_RUN_SQL), {"run_id": run_id, "source_url": source_url,
                                          "source_layer": source_layer, "dry_run": dry_run})
        upsert = _g(UPSERT_SQL)
        for batch in _batches(records, BATCH_SIZE):
            by_state: dict[str, list[dict[str, Any]]] = {}
            for record in batch:
                by_state.setdefault(record["state_code"], []).append(record)
            for state_code, group in by_state.items():
                existing = _existing_hashes(conn, state_code, [r["site_id"] for r in group])
                to_write = []
                for record in group:
                    previous = existing.get(record["site_id"])
                    if previous is None:
                        counts["inserted"] += 1
                        to_write.append(record)
                    elif previous != record["row_hash"]:
                        counts["updated"] += 1
                        to_write.append(record)
                    else:
                        counts["unchanged"] += 1
                if to_write:
                    conn.execute(upsert, [_params(r, run_id) for r in to_write])

        if report.issues:
            conn.execute(_g(INSERT_ISSUE_SQL), [{**issue, "run_id": run_id} for issue in report.issues])

        summary = {**{o.value: n for o, n in report.outcomes.items()}, **counts}
        conn.execute(_g(FINISH_RUN_SQL), {"run_id": run_id, "status": "dry_run" if dry_run else "completed",
                                          "counts": json.dumps(summary)})
        if dry_run:
            transaction.rollback()
        else:
            transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    finally:
        conn.close()

    report.outcomes.update({Outcome.INSERTED: counts["inserted"], Outcome.UPDATED: counts["updated"],
                            Outcome.UNCHANGED: counts["unchanged"]})
    return LoadResult(run_id, counts["inserted"], counts["updated"], counts["unchanged"], dry_run)
