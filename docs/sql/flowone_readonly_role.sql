-- FlowOne read-only database login (Step 7, least privilege).
--
-- Run as the PostgreSQL administrator (postgres). Safe to run more than once:
-- it creates flowone_reader if missing, otherwise just updates it, and always
-- (re)sets its password.
--
-- PowerShell, from the repository root:
--   $env:PGPASSWORD = "<postgres admin password>"
--   & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h localhost -U postgres -d MasterDB `
--       -v reader_password=<new reader password> -f docs/sql/flowone_readonly_role.sql
--   Remove-Item Env:PGPASSWORD
--
-- Then in .env:  POSTGRES_USER=flowone_reader
--                POSTGRES_PASSWORD=<new reader password>
--
-- What it does: a login that can only read the public schema. Even if a bug
-- ever produced a write statement, PostgreSQL itself would refuse it.

\set ON_ERROR_STOP on

-- Create the login only if it does not exist yet.
SELECT 'CREATE ROLE flowone_reader LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'flowone_reader')
\gexec

-- Password and limits (psql quotes the password safely via :'...').
ALTER ROLE flowone_reader
    WITH LOGIN
    PASSWORD :'reader_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 50;

-- Every session starts read-only and with bounded waits.
ALTER ROLE flowone_reader SET default_transaction_read_only = on;
ALTER ROLE flowone_reader SET statement_timeout = '30s';
ALTER ROLE flowone_reader SET lock_timeout = '5s';
ALTER ROLE flowone_reader SET idle_in_transaction_session_timeout = '60s';

GRANT CONNECT ON DATABASE :"DBNAME" TO flowone_reader;
GRANT USAGE ON SCHEMA public TO flowone_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO flowone_reader;

-- Tables created later by the owner are readable too (run as that owner).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO flowone_reader;

-- Optional hardening, affects EVERY login on this database, not only FlowOne:
-- on PostgreSQL < 15 any login may create objects in "public" by default.
-- Uncomment only after checking no other application relies on that.
-- REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Confirmation shown at the end.
SELECT rolname AS login, rolsuper AS superuser, rolcanlogin AS can_log_in
FROM pg_roles WHERE rolname = 'flowone_reader';
