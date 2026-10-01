-- Runs as the Postgres superuser on first cluster init (docker-entrypoint-initdb.d) or by a DBA.
-- Extensions are the only part of the schema that needs superuser; the application never does.
-- On managed Postgres (RDS, Neon, Supabase, ...) enable these through the provider's console/CLI instead.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
