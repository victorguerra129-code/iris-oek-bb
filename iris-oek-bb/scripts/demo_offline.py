"""Offline demo: normalize the SYNTHETIC fixture and write the quality report.

Run from anywhere (e.g. Spyder). All paths are resolved from the project root.
The fixture is synthetic: its field names are hypothetical (see config/mapping.yaml).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from oekokonto_harvester.config import load_mapping, load_settings, resolve_path  # noqa: E402
from oekokonto_harvester.normalize import process_frame  # noqa: E402
from oekokonto_harvester.quality import QualityReport  # noqa: E402

settings = load_settings("config/settings.example.yaml")
mapping = load_mapping("config/mapping.yaml")
fixture = ROOT / "tests" / "fixtures" / "sample_sites.geojson"

gdf = gpd.read_file(fixture)
report = QualityReport()
records = process_frame(gdf, mapping, settings, source_url=f"fixture://{fixture.name}", report=report)

pd.set_option("display.width", 200)
table = pd.DataFrame(records).drop(columns=["geom_25832", "source_geom_wkt", "row_hash", "source_url", "retrieved_at"])
print(table[["site_id", "municipality", "compensation_status", "area_m2", "area_geom_m2", "geometry_repaired"]].to_string(index=False))
print()
print(report.summary())

out_dir = resolve_path(settings.report_dir)
print("JSON report:", report.write_json(out_dir / "quality_report.json"))
print("Issues CSV :", report.write_issues_csv(out_dir / "quality_issues.csv"))
