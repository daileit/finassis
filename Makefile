.PHONY: help i18n i18n-check seeds-check check docs-push db-up db-down db-check db-parse

PSQL_URL ?= postgresql://finassis:finassis@localhost:5432/finassis
DB_SCRATCH ?= finassis_schema_check

PY ?= python3
DOCS_BRANCH ?= chore/refinement-loop
DOCS_WORKTREE ?= ../finassis-refine
DOCS_MSG ?= docs: update design docs

help:
	@echo "make i18n         regenerate i18n/generated/*.json from seeds/"
	@echo "make i18n-check   verify catalogue coverage, placeholders and generated freshness"
	@echo "make seeds-check  validate seeds/*.json against their JSON Schemas"
	@echo "make check        run all checks"
	@echo "make docs-push    publish docs/ (git-ignored on main) to $(DOCS_BRANCH) via a temporary worktree"
	@echo "make db-up        start Postgres 16 (pgvector) + Redis via docker compose"
	@echo "make db-down      stop them"
	@echo "make db-parse     syntax-check api/db/schema.sql with the Postgres parser (no server needed)"
	@echo "make db-check     apply schema.sql to a scratch database and run api/db/smoke.sql (needs db-up)"

db-up:
	docker compose up -d postgres redis
	@until docker compose exec -T postgres pg_isready -U finassis -d finassis >/dev/null 2>&1; do sleep 1; done; echo "postgres ready"

db-down:
	docker compose down

db-parse:
	$(PY) -c "import pglast,sys; s=open('api/db/schema.sql',encoding='utf-8').read(); print('schema.sql:', len(pglast.parse_sql(s)), 'statements parse OK')"

db-check:
	@psql "$(PSQL_URL)" -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS $(DB_SCRATCH);" -c "CREATE DATABASE $(DB_SCRATCH);"
	@psql "$(subst /finassis,/$(DB_SCRATCH),$(PSQL_URL))" -v ON_ERROR_STOP=1 -q -f api/db/schema.sql && echo "schema applied"
	@psql "$(subst /finassis,/$(DB_SCRATCH),$(PSQL_URL))" -v ON_ERROR_STOP=1 -q -f api/db/smoke.sql
	@psql "$(PSQL_URL)" -q -c "DROP DATABASE $(DB_SCRATCH);"

# docs/ is ignored on main; this syncs the working-tree docs into the docs branch
# without switching branches (a plain checkout would overwrite the ignored files).
docs-push:
	@test -d docs || (echo "no docs/ directory" && exit 1)
	@git worktree add --quiet $(DOCS_WORKTREE) $(DOCS_BRANCH) || (echo "worktree exists or branch missing: $(DOCS_WORKTREE) / $(DOCS_BRANCH)" && exit 1)
	@rsync -a --delete docs/ $(DOCS_WORKTREE)/docs/
	@cd $(DOCS_WORKTREE) && git add -A docs && \
	  if git diff --cached --quiet; then echo "docs unchanged on $(DOCS_BRANCH)"; \
	  else git commit -q -m "$(DOCS_MSG)" && git push -q origin $(DOCS_BRANCH) && echo "pushed docs to $(DOCS_BRANCH)"; fi
	@git worktree remove --force $(DOCS_WORKTREE)

i18n:
	$(PY) scripts/i18n_gen.py

i18n-check:
	$(PY) scripts/i18n_gen.py --check
	$(PY) scripts/i18n_check.py

seeds-check:
	$(PY) scripts/seeds_check.py

check: seeds-check i18n-check db-parse
