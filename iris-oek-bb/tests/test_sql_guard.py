"""The iris_staging-only policy, verified without a database."""
import pathlib

import pytest

from oekokonto_harvester import loader
from oekokonto_harvester.loader import SafetyError, get_engine, guard_sql

MIGRATIONS = sorted((pathlib.Path(__file__).resolve().parents[1] / "sql" / "migrations").glob("*.sql"))

LOADER_STATEMENTS = [loader.UPSERT_SQL, loader.INSERT_RUN_SQL, loader.INSERT_ISSUE_SQL,
                     loader.FINISH_RUN_SQL, loader.SELECT_HASHES_SQL]


@pytest.mark.parametrize("sql", LOADER_STATEMENTS)
def test_every_loader_statement_passes_the_guard(sql):
    assert guard_sql(sql) == sql


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_every_migration_statement_passes_the_guard(path):
    statements = loader._split_sql(path.read_text(encoding="utf-8"))
    assert statements
    for statement in statements:
        guard_sql(statement)


@pytest.mark.parametrize("sql", [
    "INSERT INTO public.sites VALUES (1)",
    "INSERT INTO sites VALUES (1)",
    "UPDATE iris_core.sites SET a = 1",
    "UPDATE iris_staging.a SET x = 1 FROM iris_core.b",
    "INSERT INTO iris_staging.a SELECT * FROM iris_core.b",
    "SELECT * FROM sites",
    "DELETE FROM iris_staging.a",
    "DROP TABLE iris_staging.a",
    "DROP SCHEMA iris_staging CASCADE",
    "CREATE EXTENSION postgis",
    "CREATE SCHEMA other",
    "GRANT ALL ON iris_staging.a TO someone",
    "TRUNCATE iris_core.sites",
    'INSERT INTO "iris_core".sites VALUES (1)',
    "SET search_path TO iris_core",
    "CREATE TABLE public.t (id int)",
    "CREATE INDEX i ON public.t (id)",
])
def test_unsafe_statements_are_blocked(sql):
    with pytest.raises(SafetyError):
        guard_sql(sql)


def test_text_inside_string_literals_is_ignored():
    guard_sql("SELECT 'FROM public.x DROP SCHEMA' AS note")


@pytest.mark.parametrize("url", [
    "postgresql://u:p@localhost/iris_core",
    "postgresql+psycopg://u:p@localhost/IRIS_CORE",
    "postgresql://u:p@localhost/my_prod_db",
])
def test_engine_refuses_core_and_production_databases(url):
    with pytest.raises(SafetyError):
        get_engine(url)


def test_engine_requires_database_url_and_sets_driver(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_engine()
    assert get_engine("postgresql://u:p@localhost/iris_staging_dev").dialect.driver == "psycopg"
