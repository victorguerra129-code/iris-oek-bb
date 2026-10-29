-- Example validation and spatial queries for iris_staging.compensation_sites.
-- Read-only. Run in psql, pgAdmin, DBeaver or the QGIS DB Manager SQL window.

-- 1) Idempotency evidence: counts of every load run (the second run of the same input
--    shows only "unchanged"; "inserted" must not grow).
SELECT started_at, status, dry_run, source_layer, counts
FROM iris_staging.compensation_load_runs
ORDER BY started_at;

-- 2) Key uniqueness: must return 0 rows (also enforced by the primary key).
SELECT state_code, site_id, count(*)
FROM iris_staging.compensation_sites
GROUP BY state_code, site_id
HAVING count(*) > 1;

-- 3) Geometry validity and projection: expect one row -> 25832, MULTIPOLYGON, true.
SELECT ST_SRID(geom_25832) AS srid, GeometryType(geom_25832) AS geom_type,
       ST_IsValid(geom_25832) AS is_valid, count(*) AS sites
FROM iris_staging.compensation_sites
GROUP BY 1, 2, 3;

-- 4) Declared vs geometry-derived area (m2) and repaired geometries.
SELECT site_id, area_m2 AS declared_m2, round(area_geom_m2::numeric, 1) AS from_geometry_m2,
       round((100 * (area_m2 - area_geom_m2) / NULLIF(area_geom_m2, 0))::numeric, 1) AS diff_pct,
       geometry_repaired
FROM iris_staging.compensation_sites
ORDER BY abs(area_m2 - area_geom_m2) DESC NULLS LAST;

-- 5) Sites by normalized status ('unmapped' = value not in config/mapping.yaml).
SELECT coalesce(compensation_status, '(none)') AS status, count(*) AS sites,
       round(sum(area_geom_m2)::numeric, 0) AS total_area_m2
FROM iris_staging.compensation_sites
GROUP BY 1
ORDER BY sites DESC;

-- 6) Spatial query: sites within 2 km of site BB-001 (EPSG:25832, metres), nearest first.
SELECT s.site_id, s.site_name, s.municipality,
       round(ST_Distance(s.geom_25832, ref.geom_25832)::numeric, 0) AS distance_m
FROM iris_staging.compensation_sites s
JOIN iris_staging.compensation_sites ref ON ref.state_code = 'BB' AND ref.site_id = 'BB-001'
WHERE s.site_id <> ref.site_id
  AND ST_DWithin(s.geom_25832, ref.geom_25832, 2000)
ORDER BY distance_m;

-- 7) Sample export as GeoJSON (WGS84) with provenance, ready for QGIS or a web map.
SELECT jsonb_build_object(
         'type', 'FeatureCollection',
         'features', coalesce(jsonb_agg(jsonb_build_object(
             'type', 'Feature',
             'geometry', ST_AsGeoJSON(ST_Transform(geom_25832, 4326))::jsonb,
             'properties', jsonb_build_object('state_code', state_code, 'site_id', site_id,
                 'municipality', municipality, 'status', compensation_status,
                 'area_geom_m2', round(area_geom_m2::numeric, 1), 'source_layer', source_layer))), '[]'::jsonb)
       ) AS geojson
FROM iris_staging.compensation_sites;

-- 8) Data-quality log of the latest run (rejections and warnings).
SELECT source_row, site_id, outcome, reasons, warnings
FROM iris_staging.compensation_load_issues
WHERE run_id = (SELECT run_id FROM iris_staging.compensation_load_runs ORDER BY started_at DESC LIMIT 1)
ORDER BY source_row;
