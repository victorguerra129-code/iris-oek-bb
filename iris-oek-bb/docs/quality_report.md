# Quality and error report (fixture run)

Scope: the synthetic fixture `tests/fixtures/sample_sites.geojson` (10 features, EPSG:25833). The live service
was unavailable, so **these results describe the pipeline's behaviour, not the quality of real Brandenburg data.**

## 1. Source access (evidence)

| Item | Result |
|---|---|
| `GET .../services/oekokonto_wfs?service=WFS&request=GetCapabilities` (2026-10-28, 14:40–15:00 CET) | HTTP 200, `text/html`, redirected to `geobasis-bb.de/lgb/de/dienstleister/inspire-zentrale/` |
| Control `.../services/br_natraum_wfs?request=GetCapabilities&service=WFS` | WFS XML returned |
| Conclusion | Endpoint as written is not a usable WFS from this location. Plan B (fixture demonstrator), no bypass. |

## 2. Counts by processing outcome

Run by the author (`python scripts/run.py run --source fixture --skip-load`, Windows, Python 3.11):

| Outcome | Count |
|---|---|
| processed | 10 |
| `ok` | 4 |
| `repaired` (loaded) | 1 |
| `rejected` (not loaded) | 5 |

Expected database load results, asserted by the database tests (`tests/test_loader_db.py`);
not run by the author (no PostgreSQL available):

| Load run | Expected result |
|---|---|
| run 1: `inserted` | 5 |
| run 2, same input: `unchanged` | 5 (0 inserted, 0 updated, no duplicates) |

## 3. Rejected data

| Row | Site | Reason | Why it is correct |
|---|---|---|---|
| 3 | (none) | `missing_site_id` | no stable key, cannot be upserted |
| 4 | BB-001 | `duplicate_site_id` | same key as row 0 within one harvest |
| 5 | BB-006 | `no_polygonal_geometry` | a line is not a compensation area |
| 6 | BB-007 | `missing_geometry` | no geometry to project |
| 7 | BB-008 | `outside_plausible_bounds` | coordinates far outside Brandenburg |

Each rejection keeps its original attributes and original WKT as valid JSON in `reports/quality_issues.csv`
(and, when loaded, in `compensation_load_issues.raw_properties`).

## 4. Accepted with a warning or repair

| Site | Finding | Treatment |
|---|---|---|
| BB-003 | self-intersecting ring | repaired with `make_valid`, `geometry_repaired = true` |
| BB-002 | declared 25 000 m² vs 250 000 m² from geometry (-90 %) | loaded, warning `area_mismatch`, both values kept |
| BB-009 | status "Sonderfall" not in the table; no area unit | `unmapped`; `area_m2 = NULL`; warnings `unmapped_status`, `area_unit_missing` |
| BB-010 | area "abc" | `area_m2 = NULL`, warning `unparseable_area` |
| BB-001 | declared 10 000 m² vs 10 000 m² computed in EPSG:25833 | no warning; measured in 25832 it would be ~10 014 m² (+0.14 %, Brandenburg lies outside UTM zone 32) |

## 5. Edge cases covered by tests

German decimals and thousand separators, decomposed Unicode, NaN-as-null in the error log, Z coordinates,
geometry collections, missing/forced CRS, duplicates across pages, pagination with overlapping content,
HTML and OGC exception responses, `max_pages` limit, raw-response retention, rollback after a mid-batch
constraint violation, dry run, edited migrations, structure drift, and statements aimed at other schemas.
Rollback, dry run, edited migrations and structure drift are database tests (not run by the author).

## 6. Evidence for the acceptance criteria

Database rows below are **expected results asserted by the database tests**, which the author could not run.
Everything marked "yes" was run by the author (Windows, Python 3.11).

| Criterion | Evidence | Run by the author? |
|---|---|---|
| Correct state, layer, domain; provenance retained | Normalized records carry `state_code = BB`, `source_layer`, `source_url`, `source_crs`, `source_geom_wkt`, `retrieved_at` (`test_normalize.py`); storage in the database checked by `test_provenance_and_original_geometry_are_retained` | Normalization: yes. Database: no |
| Valid projected geometry | `make_valid`, MultiPolygon, reprojection to EPSG:25832 (`test_geometry.py`); `ST_SRID = 25832` and `ST_IsValid` in the database checked by `test_stored_geometry_is_valid_multipolygon_in_25832` and `example_queries.sql` #3 | Geometry processing: yes. Database: no |
| Re-running creates no duplicates | Expected: run 2 reports 5 `unchanged`, table keeps 5 rows (`test_reloading_the_same_input_creates_no_duplicates`, `example_queries.sql` #1 and #2; Docker loads the fixture twice) | No |
| Quality issues reported | `reports/quality_report.json`, `reports/quality_issues.csv` (valid JSON, original WKT); table `compensation_load_issues` | Reports: yes. Database table: no |
| Writes only to `iris_staging` | SQL guard tests (`test_sql_guard.py`); statement-capture, pre-flight and least-privilege checks are database tests | Guard tests: yes. Database checks: no |



## 7. Test results

pytest (author, Windows, Python 3.11): 84 passed, 10 skipped (database tests need DATABASE_URL). The 10 database tests and the Docker files were prepared and reviewed with an AI coding assistant; the author could not run them (no admin rights to install PostgreSQL/Docker).

## 8. Known gaps

Real schema and data unverified; GML reading unexercised; live paging behaviour unknown. Docker files and the
10 database tests were prepared and reviewed with an AI coding assistant; the author could not run them
(no admin rights to install PostgreSQL/Docker). On networks with an HTTP proxy, the WFS client tests
(local mock server on 127.0.0.1) need NO_PROXY=127.0.0.1,localhost.