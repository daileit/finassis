-- Canonical list of extensions Finassis needs. Must be created INSIDE the application database by a superuser:
--   psql -U postgres -d <your_app_db> -f api/db/extensions.sql
-- In compose this is done by api/db/init/01-finassis.sh; on managed Postgres use the provider's console.
-- Extensions are per-database and need superuser; the application never has it (schema.sql only checks they exist).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
