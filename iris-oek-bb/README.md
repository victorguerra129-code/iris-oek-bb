# Brandenburg compensation-site WFS harvester (IRIS-OEK-BB, Task 057)

Harvests compensation / eco-account sites (WFS layer `bb_flaechenpool`) and loads them
idempotently into `iris_staging.compensation_sites` (PostgreSQL/PostGIS).

> **Fixture-based demonstrator.** On 2026-10-28 (14:40–15:00 CET) the endpoint
> `https://inspire.brandenburg.de/services/oekokonto_wfs` returned an HTML page instead of WFS XML.
> A control request to another Brandenburg WFS (`br_natraum_wfs`) worked. Nothing was bypassed.
> The pipeline runs on a synthetic fixture; source field names are placeholders (`config/mapping.yaml`).
> Evidence: `exploration/`.

## What it does
1. Discovers the WFS (capabilities, layer check, paging) and pages through GetFeature.
2. Normalizes names, status, area (m²) and eco-credit fields without guessing missing values.
3. Repairs geometries, keeps the original CRS and WKT, stores geometry in EPSG:25832.
4. Upserts on (`state_code`, `site_id`) in one transaction, writing only to `iris_staging`.
5. Reports outcomes and issues (`reports/`, table `compensation_load_issues`).

## Run (Python 3.11+)
```
pip install -r requirements-dev.txt
python scripts/run.py run --source fixture --skip-load   # no database needed
python -m pytest                                         # database tests skip without DATABASE_URL
```
With an isolated PostGIS database: run `sql/00_prerequisites.sql` once as admin (optionally `sql/roles.sql`),
set `DATABASE_URL`, then `python scripts/run.py init-db` and `python scripts/run.py run --source fixture`
(run it twice: the second run reports only `unchanged`).

Docker: copy `.env.example` to `.env`, set `POSTGRES_PASSWORD`, then
`docker compose up --build --abort-on-container-exit app` and `docker compose run --rm tests`.

Validation and spatial queries: `sql/example_queries.sql`. Quality report: `docs/quality_report.md`.

## Verification status
| Part | Status |
|---|---|
| Normalization, geometry, reports, unit tests | Run by the author: Windows, Python 3.11, `84 passed, 10 skipped` |
| Database tests (10) and Docker files | Prepared and reviewed with an AI coding assistant; not run by the author (no admin rights to install PostgreSQL/Docker) |
| Live WFS | Not available (see above) |

## Assumptions and limitations
- The real source schema is unknown; the mapping is hypothetical and lives only in `config/mapping.yaml`.
- Target columns beyond `site_id`/`state_code` are a proposal (`sql/migrations/001_initial.sql`).
- Geometry is stored in EPSG:25832 as required; `area_geom_m2` is measured in EPSG:25833
  (Brandenburg's UTM zone) to avoid ~0.2–0.3 % scale distortion.
- Only WFS 2.0 paging; GML reading not exercised; sites removed at the source are not flagged.
- In Docker the harvester connects as the database owner; the least-privilege role is not wired in.

Full details (mapping table, processing rules, safety controls): `docs/technical_notes.md`.
