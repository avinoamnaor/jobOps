-- Runs exactly once, when the postgres data volume is first created.
--
-- Phase 1 tests run against a real PostgreSQL database rather than SQLite, so
-- that we never get a dialect surprise between tests and development. Creating
-- the test database here means it exists from day one.
--
-- If you add something to this file later, it will NOT run against an existing
-- volume. Either run the SQL by hand, or destroy the volume with
-- `docker compose down -v` (which deletes all local data) and start again.

CREATE DATABASE jobops_test OWNER jobops;
