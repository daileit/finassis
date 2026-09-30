.PHONY: help i18n i18n-check seeds-check check

PY ?= python3

help:
	@echo "make i18n         regenerate i18n/generated/*.json from seeds/"
	@echo "make i18n-check   verify catalogue coverage, placeholders and generated freshness"
	@echo "make seeds-check  validate seeds/*.json against their JSON Schemas"
	@echo "make check        run all checks"

i18n:
	$(PY) scripts/i18n_gen.py

i18n-check:
	$(PY) scripts/i18n_gen.py --check
	$(PY) scripts/i18n_check.py

seeds-check:
	$(PY) scripts/seeds_check.py

check: seeds-check i18n-check
