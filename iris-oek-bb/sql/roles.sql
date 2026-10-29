-- OPTIONAL, one-time administrator step: a least-privilege role for the harvester.
-- Replace CHANGE_ME locally. Never commit a real password.
-- Run AFTER 00_prerequisites.sql, with psql / pgAdmin / DBeaver.

CREATE ROLE iris_oek_loader LOGIN PASSWORD 'CHANGE_ME';

GRANT USAGE, CREATE ON SCHEMA iris_staging TO iris_oek_loader;

-- Explicitly remove any access to iris_core if that schema exists in this database.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'iris_core') THEN
        REVOKE ALL ON SCHEMA iris_core FROM iris_oek_loader;
        REVOKE ALL ON ALL TABLES IN SCHEMA iris_core FROM iris_oek_loader;
    END IF;
END
$$;
