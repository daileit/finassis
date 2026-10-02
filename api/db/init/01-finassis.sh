#!/bin/bash
# Provision Finassis on a Postgres cluster. Runs as the superuser:
#   - automatically, ONCE, on an empty volume via /docker-entrypoint-initdb.d/ (compose)
#   - or by hand, any time, on an existing cluster:  docker compose exec postgres bash /docker-entrypoint-initdb.d/01-finassis.sh
# Idempotent. Creates the application role, the application database (owned by that role) and the
# extensions INSIDE that database (extensions are per-database and need superuser; the app never has it).
#
# Env (defaults are dev-only — set real values in production):
#   FINASSIS_DB_NAME      database name            (default: finassis)
#   FINASSIS_DB_USER      application role         (default: finassis_app)
#   FINASSIS_DB_PASSWORD  application role password(default: finassis_app)
#   FINASSIS_DB_EXTENSIONS space-separated list    (default: vector pg_trgm unaccent btree_gist pgcrypto)
# POSTGRES_USER / POSTGRES_DB come from the postgres image and are only used to connect as superuser.
set -euo pipefail

DB="${FINASSIS_DB_NAME:-finassis}"
ROLE="${FINASSIS_DB_USER:-finassis_app}"
PW="${FINASSIS_DB_PASSWORD:-finassis_app}"
EXTS="${FINASSIS_DB_EXTENSIONS:-vector pg_trgm unaccent btree_gist pgcrypto}"
SU="${POSTGRES_USER:-postgres}"
MAINT_DB="${POSTGRES_DB:-postgres}"

echo "finassis: provisioning role '$ROLE' and database '$DB'"

# 1. role (create or update password)
psql -v ON_ERROR_STOP=1 -U "$SU" -d "$MAINT_DB" -v role="$ROLE" -v pw="$PW" <<-'EOSQL'
	SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', :'role', :'pw')
	 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
	SELECT format('ALTER ROLE %I WITH PASSWORD %L', :'role', :'pw')
	 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
	ALTER ROLE :"role" SET statement_timeout = '30s';
	ALTER ROLE :"role" SET idle_in_transaction_session_timeout = '60s';
EOSQL

# 2. database owned by the role (CREATE DATABASE cannot run inside a transaction/DO block)
if ! psql -U "$SU" -d "$MAINT_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 -U "$SU" -d "$MAINT_DB" -c "CREATE DATABASE \"$DB\" OWNER \"$ROLE\";"
fi
psql -v ON_ERROR_STOP=1 -U "$SU" -d "$MAINT_DB" -c "ALTER DATABASE \"$DB\" SET timezone = 'UTC';"

# 3. inside the database: schema access for the role + the extensions
psql -v ON_ERROR_STOP=1 -U "$SU" -d "$DB" -c "GRANT ALL ON SCHEMA public TO \"$ROLE\";"
for ext in $EXTS; do
  psql -v ON_ERROR_STOP=1 -U "$SU" -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS \"$ext\";"
done

echo "finassis: ready — connect with postgresql://$ROLE:***@<host>:5432/$DB"
psql -U "$SU" -d "$DB" -c '\dx' | sed 's/^/  /'
