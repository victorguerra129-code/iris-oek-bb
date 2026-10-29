"""Shared fixtures. Tests never touch the network; database tests need DATABASE_URL."""
import os
import sys
from pathlib import Path

import geopandas as gpd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oekokonto_harvester.config import load_mapping, load_settings  # noqa: E402
from oekokonto_harvester.normalize import process_frame  # noqa: E402
from oekokonto_harvester.quality import QualityReport  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sample_sites.geojson"


@pytest.fixture(scope="session")
def settings():
    return load_settings("config/settings.example.yaml")


@pytest.fixture(scope="session")
def mapping():
    return load_mapping("config/mapping.yaml")


@pytest.fixture()
def fixture_gdf():
    return gpd.read_file(FIXTURE)


@pytest.fixture()
def processed(fixture_gdf, mapping, settings):
    """(accepted records, report) for the synthetic fixture."""
    report = QualityReport()
    records = process_frame(fixture_gdf, mapping, settings, "fixture://sample_sites.geojson", report,
                            retrieved_at="2026-10-28T12:00:00+00:00")
    return records, report


@pytest.fixture(scope="session")
def db_engine():
    """Engine for an isolated test database; the whole module is skipped without DATABASE_URL."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    from oekokonto_harvester.loader import apply_migrations, get_engine

    engine = get_engine()
    apply_migrations(engine)          # idempotent; fails loudly if prerequisites are missing
    return engine


@pytest.fixture()
def clean_db(db_engine):
    from sqlalchemy import text

    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE iris_staging.compensation_load_issues, "
                          "iris_staging.compensation_sites, iris_staging.compensation_load_runs CASCADE"))
    return db_engine
