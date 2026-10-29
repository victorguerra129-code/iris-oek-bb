"""Command line interface: initialise the schema and run the harvest/load pipeline."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests

from .config import Mapping, Settings, load_mapping, load_settings, resolve_path
from .loader import apply_migrations, check_database, get_engine, load_records
from .normalize import process_frame
from .quality import QualityReport
from .wfs_client import WfsClient, WfsError

log = logging.getLogger("oekokonto_harvester")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _harvest_live(settings: Settings, mapping: Mapping, report: QualityReport) -> list[dict]:
    client = WfsClient(settings)
    info = client.discover()
    type_name = client.resolve_type_name(info)
    log.info("WFS %s | paging=%s | CRS=%s", info.version, info.supports_paging,
             info.feature_types[type_name]["default_crs"])
    retrieved_at = _now()
    records: list[dict] = []
    for start, gdf, url in client.iter_pages(type_name):
        log.info("page startIndex=%s features=%s", start, len(gdf))
        records.extend(process_frame(gdf, mapping, settings, url, report, retrieved_at, row_offset=start))
    return records


def _harvest_fixture(path: Path, settings: Settings, mapping: Mapping, report: QualityReport) -> tuple[list[dict], str]:
    source_url = f"fixture://{path.name}"
    gdf = gpd.read_file(path)
    return process_frame(gdf, mapping, settings, source_url, report, _now()), source_url


def cmd_init_db(args: argparse.Namespace) -> int:
    applied = apply_migrations(get_engine(), args.migrations)
    print(f"Applied migrations: {applied}" if applied else "No pending migrations.")
    return 0


def cmd_check_db(args: argparse.Namespace) -> int:
    results = check_database(get_engine())
    for name, passed, detail in results:
        print(f"[{'OK' if passed else 'FAIL'}] {name}" + ("" if passed else f": {detail}"))
    return 0 if all(passed for _, passed, _ in results) else 1


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)
    mapping = load_mapping(args.mapping)
    report = QualityReport()

    try:
        if args.source == "fixture":
            records, source_url = _harvest_fixture(resolve_path(args.fixture), settings, mapping, report)
        else:
            records = _harvest_live(settings, mapping, report)
            source_url = settings.service_url
    except (WfsError, requests.RequestException) as exc:
        print(f"WFS ERROR: {exc}\nThe live service is not usable; use --source fixture to run the demonstrator.")
        return 2

    summary = report.summary()
    run_info: dict = {"source": args.source, "source_url": source_url, "layer": settings.layer}
    if not args.skip_load:
        result = load_records(get_engine(), records, report, source_url, settings.layer, dry_run=args.dry_run)
        run_info.update(run_id=result.run_id, dry_run=result.dry_run)
        summary = report.summary()

    out_dir = resolve_path(settings.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "quality_report.json").write_text(
        json.dumps({"run": run_info, **summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    report.write_issues_csv(out_dir / "quality_issues.csv")
    print(json.dumps({"run": run_info, **summary}, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oekokonto_harvester", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="apply pending SQL migrations to iris_staging")
    init.add_argument("--migrations", default="sql/migrations")
    init.set_defaults(func=cmd_init_db)

    check = sub.add_parser("check-db", help="verify target isolation, migrations and table structure")
    check.set_defaults(func=cmd_check_db)

    run = sub.add_parser("run", help="harvest, normalize and load")
    run.add_argument("--source", choices=["live", "fixture"], default="fixture")
    run.add_argument("--fixture", default="tests/fixtures/sample_sites.geojson")
    run.add_argument("--settings", default="config/settings.example.yaml")
    run.add_argument("--mapping", default="config/mapping.yaml")
    run.add_argument("--dry-run", action="store_true", help="load inside a transaction, then roll back")
    run.add_argument("--skip-load", action="store_true", help="normalize and report only, no database")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
