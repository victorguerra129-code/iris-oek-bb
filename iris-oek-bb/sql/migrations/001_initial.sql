-- Migration 001: initial iris_staging tables for the Brandenburg compensation-site load.
-- Proposed design: the assignment names the target table but does not define its columns.
-- Applied once and recorded in iris_staging.schema_migrations. Never edit it after it has
-- been applied: add a new numbered migration instead. Touches only iris_staging.
-- Prerequisites (PostGIS, the schema) live in 00_prerequisites.sql.

CREATE TABLE iris_staging.compensation_load_runs (
    run_id        uuid PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    source_url    text NOT NULL,
    source_layer  text NOT NULL,
    dry_run       boolean NOT NULL DEFAULT false,
    status        text NOT NULL DEFAULT 'running',
    counts        jsonb
);

CREATE TABLE iris_staging.compensation_sites (
    state_code               text NOT NULL,
    site_id                  text NOT NULL,
    site_name                text,
    municipality             text,
    cadastral_district       text,
    compensation_status_raw  text,
    compensation_status      text,
    area_source_value        double precision,
    area_source_unit         text,
    area_m2                  double precision,
    area_geom_m2             double precision,
    eco_credit_fields        jsonb,
    geom_25832               geometry(MultiPolygon, 25832) NOT NULL,
    source_crs               text NOT NULL,
    source_geom_wkt          text NOT NULL,
    geometry_repaired        boolean NOT NULL DEFAULT false,
    source_url               text NOT NULL,
    source_layer             text NOT NULL,
    retrieved_at             timestamptz NOT NULL,
    row_hash                 text NOT NULL,
    first_loaded_at          timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    last_run_id              uuid REFERENCES iris_staging.compensation_load_runs (run_id),
    PRIMARY KEY (state_code, site_id),
    CONSTRAINT compensation_sites_state_upper CHECK (state_code = upper(state_code)),
    CONSTRAINT compensation_sites_geom_valid CHECK (ST_IsValid(geom_25832)),
    CONSTRAINT compensation_sites_area_nonneg CHECK (area_m2 IS NULL OR area_m2 >= 0)
);

CREATE INDEX compensation_sites_geom_gix
    ON iris_staging.compensation_sites USING gist (geom_25832);

CREATE INDEX compensation_sites_status_idx
    ON iris_staging.compensation_sites (compensation_status);

CREATE TABLE iris_staging.compensation_load_issues (
    issue_id        bigserial PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES iris_staging.compensation_load_runs (run_id),
    source_row      integer,
    site_id         text,
    outcome         text NOT NULL,
    reasons         text,
    warnings        text,
    raw_properties  jsonb
);

CREATE INDEX compensation_load_issues_run_idx
    ON iris_staging.compensation_load_issues (run_id);
