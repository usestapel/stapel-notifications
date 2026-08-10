# stapel-notifications — contract emission + drift gate (contract-pipeline.md §2-3).
#
# This module emits its OWN contract triad (schema.json + flows.json + errors.json)
# per-module, byte-identical to the monolith aggregate's notifications slice, from
# a single-module {notifications + core} Django instance mounted at the canonical
# /notifications/api/ prefix (see _codegen.py / _codegen_settings.py /
# codegen_urls.py). Copied from stapel-auth's etalon.
#
# PYTHON must have the module + its deps importable (the workspace venv, or a CI
# venv). The authoritative CI gate is tests/test_contract.py (run under pytest);
# these targets are the dev-loop convenience.
PYTHON ?= python3

.PHONY: contract contract-check

# Emit the contract triad + capabilities.json + the fifth artifact docs/llms.txt
# (stapel_tools.llms_txt — the module's own context slice for an agent, rendered
# from capabilities.json + the triad; badge-canon §3) into docs/.
#
# The usage-surface section (services.py + routing.py + the 23 packaged email
# templates — 30 entries: the dispatch entry point, the routing readers, and
# one line per ready-made letter so "is there already an email for X" has an
# answer) does not fit the generator's default 4000-token budget (~4320 tokens
# at honest intent length; ~5005 once the unsubscribe policy added its three
# predicates). The owner's call, the same exception stapel-auth
# and stapel-workspaces already take: raise the ceiling for this module rather
# than shorten intents to fit — a trimmed-to-fit context file is
# indistinguishable from a complete one at the point of use, which is the
# failure mode the hard-budget gate exists to prevent. contract-check below
# enforces the same ceiling; it does not disable the check.
#
# The SIXTH artifact, docs/templates.json (stapel_tools.template_contract):
# notification type -> template path -> the context variables this library
# actually passes, derived from routing.py, translation_keys.py and the AST of
# the render call site in services.py. It is emitted independently of the
# triad — it needs no Django settings and no drf-spectacular, only the module
# importable — so a host can regenerate and diff it on any interpreter.
contract:
	$(PYTHON) -m stapel_notifications._codegen --out docs
	$(PYTHON) -m stapel_notifications._capabilities --out docs
	$(PYTHON) -m stapel_notifications._template_contract --out docs
	$(PYTHON) -m stapel_tools.llms_txt . --out docs --budget 5200

# Drift gate: regenerate into a temp dir and diff against the committed docs/*.json
# (mirrors the monolith's `make codegen-check` and the frontend's `gen:*:check`).
# Everything lands under $tmp/docs so stapel_tools.llms_txt (which reads
# <repo>/docs/capabilities.json) can render against the freshly regenerated triad.
contract-check:
	@tmp=$$(mktemp -d); \
	mkdir -p "$$tmp/docs"; \
	$(PYTHON) -m stapel_notifications._codegen --out "$$tmp/docs" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_notifications._capabilities --out "$$tmp/docs" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_notifications._template_contract --out "$$tmp/docs" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_tools.llms_txt "$$tmp" --out "$$tmp/docs" --budget 5200 || { rm -rf "$$tmp"; exit 1; }; \
	rc=0; \
	for f in schema.json flows.json errors.json capabilities.json templates.json llms.txt; do \
		if ! diff -q "docs/$$f" "$$tmp/docs/$$f" >/dev/null 2>&1; then \
			echo "DRIFT: docs/$$f is stale — run 'make contract' and commit it"; \
			diff "docs/$$f" "$$tmp/docs/$$f" | head -20; rc=1; \
		fi; \
	done; \
	rm -rf "$$tmp"; \
	if [ $$rc -eq 0 ]; then echo "contract-check: docs/{schema,flows,errors,capabilities,templates,llms.txt} up to date"; fi; \
	exit $$rc


.PHONY: migration-lint

# Expand/contract gate for Django migrations (release-management.md §3;
# stapel_tools.migration_lint). Requires stapel-tools importable (the
# workspace venv, or `pip install stapel-tools` once published).
migration-lint:
	$(PYTHON) -m stapel_tools.migration_lint . --strict
