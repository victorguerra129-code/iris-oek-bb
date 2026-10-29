# Technical notes

Brandenburg Land Pool & Peat Restoration: compensation-site WFS harvester. Short overview: `README.md`.

Task `IRIS-OEK-BB` (TASK 057). A reproducible harvester that maps registered
compensation / eco-account sites from an OGC WFS (layer `bb_flaechenpool`) to
`iris_staging.compensation_sites` in PostgreSQL/PostGIS.

> **Read this first.** The live service could **not** be used: on 2026-10-28 the endpoint named in the
> assignment answered with an HTML page instead of WFS XML (details below). This repository is therefore a
> **fixture-based demonstrator**, as the assignment allows. All source field names are hypothetical
> placeholders and must be checked against the real schema before any live run. Nothing was bypassed.

## Status at a glance

| Area | State | Evidence |
|---|---|---|
| Live WFS access | **Not available** (HTML redirect) | `exploration/`, section "Data provenance" |
| Harvest, normalization, geometry repair, EPSG:25832 | Implemented, tested on synthetic data | `tests/`, `docs/quality_report.md` |
| WFS client (capabilities, paging, error handling) | Implemented, tested **only against a local mock server** | `tests/test_wfs_client.py` |
| Load into PostGIS (idempotent upsert, rollback, error log) | Implemented; database tests prepared with an AI coding assistant, **not run by the author** | `tests/test_loader_db.py` |
| `iris_staging`-only guarantees | Implemented in code and tested | "Safety controls" |
| Docker / docker-compose | Written, **not executed by the author** (Docker could not be installed) | see "Limitations" |

## Data provenance

* **Endpoint given in the assignment:** `https://inspire.brandenburg.de/services/oekokonto_wfs`, layer `bb_flaechenpool`.
* **Test, 2026-10-28 (14:40–15:00 CET):** `GetCapabilities` (`?service=WFS&request=GetCapabilities`)
  returned HTTP 200 with `text/html`. The request was redirected to
  `https://geobasis-bb.de/lgb/de/dienstleister/inspire-zentrale/`, an information page, not WFS XML.
* **Control:** `https://inspire.brandenburg.de/services/br_natraum_wfs?request=GetCapabilities&service=WFS`
  (a different Brandenburg INSPIRE service) returned WFS XML in the same session, so the server and the network
  were reachable; only the assignment's path did not behave like a WFS.
* **Evidence kept in `exploration/`:** `capabilities.xml` (the HTML that was returned), `control_br_natraum_capabilities.xml`,
  `notes.txt`, and `explore_wfs.py`.
* **Decision:** no alternative URLs were probed and nothing was circumvented. The pipeline runs on a synthetic
  fixture (`tests/fixtures/sample_sites.geojson`, EPSG:25833, deliberately containing edge cases).
  The fixture is **not real data**; its attribute names (`id`, `gemeinde`, `flaeche`, ...) are invented.
* Licence and terms of use of the real data were not verified.

## How it works

```
WFS (live)  or  GeoJSON fixture
        |  wfs_client.py: GetCapabilities, layer check, paged GetFeature (HTML / OGC exceptions rejected)
        v
normalize.py + geometry.py: text/number/status/area normalization, make_valid, MultiPolygon, EPSG:25832
        |                    -> accepted records + quality.py outcomes and reasons
        v
loader.py: one transaction: run row, upsert (insert / update / unchanged by row_hash), issue log
        v
iris_staging.compensation_sites  (+ compensation_load_runs, compensation_load_issues)
```

Repository layout:

```
config/        mapping.yaml (field mapping, status table, units), settings.example.yaml
sql/           00_prerequisites.sql (admin, once), roles.sql (optional), migrations/001_initial.sql, example_queries.sql
src/oekokonto_harvester/   config, wfs_client, normalize, geometry, quality, loader, cli
scripts/       run.py (CLI launcher), demo_offline.py
tests/         pytest suite + fixtures/sample_sites.geojson
docs/          quality_report.md, technical_notes.md (this file)
reports/       generated quality_report.json / quality_issues.csv (git-ignored)
exploration/   evidence of the live-access test
```

## Run it

Python 3.11+ is required (run by the author on Windows, Python 3.11).

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

**1. Without a database** (normalization, geometry, report):

```bash
python scripts/run.py run --source fixture --skip-load
```

Output: counts per outcome on screen and in `reports/quality_report.json`; every rejection/warning in `reports/quality_issues.csv`.

**2. With PostgreSQL + PostGIS** (use an **isolated** database; its name may not be `iris_core` or contain `prod`):

```bash
# One-time, as an administrator (psql, pgAdmin, DBeaver...): PostGIS + the iris_staging schema
#   sql/00_prerequisites.sql
# Optional, recommended: least-privilege role (edit the password first)
#   sql/roles.sql
export DATABASE_URL="postgresql+psycopg://USER:PASSWORD@localhost:5432/DBNAME"   # PowerShell: $env:DATABASE_URL = "..."
python scripts/run.py init-db       # applies pending migrations to iris_staging
python scripts/run.py check-db      # isolation, migrations, table structure
python scripts/run.py run --source fixture            # load
python scripts/run.py run --source fixture            # again: all rows 'unchanged', no duplicates
python scripts/run.py run --source fixture --dry-run  # full load inside a transaction, then rolled back
```

Then run the queries in `sql/example_queries.sql` (idempotency evidence, key uniqueness, geometry validity,
declared vs computed area, a 2 km spatial query, GeoJSON sample export, issue log).

**3. Docker** (not executed by the author, see Limitations): `cp .env.example .env`, set `POSTGRES_PASSWORD`, then
`docker compose up --build --abort-on-container-exit app` and `docker compose run --rm tests`.

**4. Tests:** `python -m pytest -q` runs 84 tests and skips the 10 database tests; with `DATABASE_URL` set
(isolated database, prerequisites applied) the database tests run as well.

**5. Live mode** (only if the real service becomes available): `python scripts/run.py run --source live`.
If the service is not a usable WFS the command stops with a clear message and exit code 2.

## Configuration

* `config/settings.example.yaml`: URL, layer, page size, delay between requests, retries, bounds, tolerances. No secrets.
* `config/mapping.yaml`: **the file to edit when the real schema is known**: source attribute names, status
  equivalences, area units, ecological-credit fields. No code change needed.
* Database credentials come only from the `DATABASE_URL` environment variable.

## Schema mapping (target table: proposed design)

The assignment names `iris_staging.compensation_sites` and the keys `site_id`/`state_code` but not the other
columns; the columns below are a proposal and should be aligned with the owning team's schema.

| Target column | Source / rule | If missing |
|---|---|---|
| `state_code` | constant `BB` from `mapping.yaml` | n/a |
| `site_id` | source identifier, trimmed; primary key with `state_code` | record rejected |
| `site_name`, `municipality`, `cadastral_district` | text, Unicode NFC, whitespace collapsed (umlauts kept) | `NULL` |
| `compensation_status_raw` / `compensation_status` | original text / value from the equivalence table, else `unmapped` | `NULL` |
| `area_source_value`, `area_source_unit`, `area_m2` | declared area; converted only with an explicit known unit | `NULL`, never guessed |
| `area_geom_m2` | area of the geometry measured in EPSG:25833 (Brandenburg's UTM zone; storage stays in 25832) | n/a |
| `eco_credit_fields` (jsonb) | listed credit attributes copied as received | `NULL`, never estimated |
| `geom_25832` | repaired MultiPolygon in EPSG:25832 | record rejected |
| `source_crs`, `source_geom_wkt` | original CRS and untouched original geometry | record rejected without CRS |
| `geometry_repaired` | `true` when `make_valid` was needed | n/a |
| `source_url`, `source_layer`, `retrieved_at`, `row_hash` | provenance and change detection | always set |

## Processing rules

* **Rejected** (not loaded, logged with a reason): `missing_site_id`, `duplicate_site_id`, `missing_geometry`,
  `missing_crs`, `no_polygonal_geometry`, `invalid_after_reprojection`, `outside_plausible_bounds`.
* **Accepted with warning:** `unmapped_status`, `area_unit_missing`, `area_unit_unknown`, `unparseable_area`,
  `negative_area`, `area_mismatch` (declared vs computed area differ by more than 10 %).
* **Outcomes:** `ok`, `repaired` (geometry repaired, still loaded), `rejected`; load outcomes `inserted`, `updated`, `unchanged`.
* **Geometry:** invalid geometries are repaired with `make_valid`; only polygonal parts are kept; Z is dropped;
  everything becomes a MultiPolygon; reprojection to EPSG:25832 with pyproj; the original WKT is preserved.

## Load semantics and idempotency

Sites are identified by `(state_code, site_id)`. Before writing, stored `row_hash` values (hash of the normalized
attributes plus the original geometry) are compared: same hash = `unchanged` (not rewritten), different = `updated`,
absent = `inserted`. The whole load (run row, sites, issues) is **one transaction**: any error rolls everything back.
Re-running the same input creates no duplicates (verified in `tests/test_loader_db.py`).

## Safety controls: writing only to `iris_staging`

Migrations being re-runnable does **not** by itself keep writes inside `iris_staging`; that is enforced in Python:

1. `guard_sql` inspects every statement the loader executes and raises `SafetyError` for any write/DDL aimed at
   another schema, any `DROP`, `DELETE`, `GRANT`, `CREATE EXTENSION/SCHEMA`, `COPY`, quoted identifiers or `search_path` changes.
2. `search_path` is pinned to `iris_staging, public` per transaction (`public` only for PostGIS types/functions).
3. Pre-flight check: the run is refused if the database name is `iris_core` or contains `prod`, if `iris_staging`
   is missing, or if a schema `iris_core` exists and the connected role can write to it.
4. `CREATE EXTENSION postgis` (which installs objects in `public`) is **not** run by the harvester; it is a separate
   administrator step (`sql/00_prerequisites.sql`).
5. `sql/roles.sql` creates a role that only has rights on `iris_staging`. This is the real protection; the code checks are a second layer.
6. Migrations are versioned (`sql/migrations/`, table `iris_staging.schema_migrations` with checksums). An applied
   migration that was edited is rejected, and the loader refuses to run on a structure that does not match what it writes.
7. Tests prove it: every statement executed during a real load is re-checked by the guard, and unsafe statements are blocked.

## Assumptions and limitations

* The real WFS schema, CRS, paging behaviour and attribute semantics are **unknown**; the mapping is hypothetical.
* Reading real GML relies on GDAL via pyogrio and was not exercised: tests use GeoJSON served by a mock server.
* Only WFS 2.0.0 paging (`count`/`startIndex`) is implemented; paging stability depends on the server's ordering unless `sort_by` is set.
* `parse_number` handles `1.234,5` and `2,5`; a lone `1.234` is read as 1.234, not 1234.
* `plausible_bounds` (a rectangle around Brandenburg in EPSG:25832) and the 10 % area tolerance are heuristics, configurable.
* `row_hash` ignores `retrieved_at` and `source_url`, so a change only in those is not an update.
* Sites that disappear from the source are not deleted or flagged (no soft-delete logic).
* A load keeps all accepted records in memory and uses one transaction; fine for thousands of polygons, not for millions.
* A superuser role can be refused by the pre-flight check when `iris_core` exists (it can write there); use the least-privilege role.
* Docker files and the 10 database tests were prepared and reviewed with an AI coding assistant; the author could not run them (no admin rights to install PostgreSQL/Docker). The author ran the offline pipeline and the unit tests on Windows, Python 3.11.
* In Docker the harvester connects as the database owner; the least-privilege role (`sql/roles.sql`) is not wired in.
