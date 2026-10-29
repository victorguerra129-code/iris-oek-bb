-- ONE-TIME administrator step (run with a privileged role, e.g. in psql, pgAdmin or DBeaver).
-- The harvester never runs this file: CREATE EXTENSION installs objects in the public
-- schema, which is outside iris_staging, so it must stay an explicit admin decision.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS iris_staging;
