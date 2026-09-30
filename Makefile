.PHONY: help i18n i18n-check seeds-check check docs-push

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

check: seeds-check i18n-check
