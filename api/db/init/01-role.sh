#!/bin/bash
# Runs ONCE as the Postgres superuser on first cluster init (docker-entrypoint-initdb.d), or by a DBA.
# Creates the single application role (non-superuser) and lets it create objects in schema public.
# Tenant isolation does not depend on DB roles: RLS policies read app.user_id, and privileged code
# paths set app.bypass_rls per transaction (see api/db/schema.sql §2 and docs/tech/08-database.md).
#
# Env: FINASSIS_DB_USER (default finassis_app), FINASSIS_DB_PASSWORD (default finassis_app — change in prod)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v role="${FINASSIS_DB_USER:-finassis_app}" \
  -v pw="${FINASSIS_DB_PASSWORD:-finassis_app}" \
  -v dbname="$POSTGRES_DB" <<-'EOSQL'
	SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', :'role', :'pw')
	 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
	GRANT CONNECT ON DATABASE :"dbname" TO :"role";
	GRANT ALL ON SCHEMA public TO :"role";
	ALTER ROLE :"role" SET statement_timeout = '30s';
	ALTER ROLE :"role" SET idle_in_transaction_session_timeout = '60s';
	ALTER DATABASE :"dbname" SET timezone = 'UTC';
EOSQL

echo "finassis application role ready: ${FINASSIS_DB_USER:-finassis_app}"
