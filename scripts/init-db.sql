-- Second Brain — one-time database bootstrap for a native PostgreSQL install.
--
-- Creates the application role, the database that DATABASE_URL in .env points
-- at, a separate test database, and the two extensions the schema depends on. Idempotent: safe
-- to re-run. Run it as the postgres superuser:
--
--   Windows (PowerShell):
--     & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h 127.0.0.1 -f scripts\init-db.sql
--
-- Credentials are the local-development defaults and match .env.example; change
-- both together if you want something else. The server only listens on loopback.
--
-- Why extensions are created here: `vector` is not a "trusted" extension, so only
-- a superuser can create it. The application role is deliberately not a
-- superuser. The Alembic migration still says CREATE EXTENSION IF NOT EXISTS,
-- which is a no-op here and does the real work on a Docker setup where the
-- application role is a superuser.

\set ON_ERROR_STOP on

SELECT 'CREATE ROLE secondbrain LOGIN PASSWORD ''secondbrain'''
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'secondbrain')
\gexec

SELECT 'CREATE DATABASE secondbrain OWNER secondbrain'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'secondbrain')
\gexec

-- A separate database for the integration tests, so `pytest` never touches
-- your real library and never races the running worker.
SELECT 'CREATE DATABASE secondbrain_test OWNER secondbrain'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'secondbrain_test')
\gexec

\connect secondbrain

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\connect secondbrain_test

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\echo Role "secondbrain", databases "secondbrain" + "secondbrain_test" and extensions are ready.
