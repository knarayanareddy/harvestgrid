SHELL := /bin/bash
UV ?= uv
PIP_AUDIT ?= $(UV) run pip-audit
PIP_AUDIT_ARGS ?=

.PHONY: bootstrap setup dev lint typecheck test-unit test-contract test-integration test-e2e test-infra test-security test-web build deploy evidence \
        web-dev web-build-demo compose-up compose-down compose-logs demo-record demo-selftest

bootstrap:
	$(UV) --version
	$(UV) sync --frozen

setup: bootstrap
	@test -f .env.local || cp .env.example .env.local
	@echo "Local fixture configuration ready. No Ring, AWS, database, or media processing was started."

dev:
	$(UV) run python -m harvestgrid_domain.walking_skeleton

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck:
	$(UV) run mypy packages/domain packages/matching packages/vision packages/weather-client services/ring-adapter services/forecasting services/notifications services/api packages/testkit

test-unit:
	$(UV) run pytest tests/unit --cov=harvestgrid_domain --cov=harvestgrid_domain_matching --cov=harvestgrid_vision --cov=harvestgrid_weather --cov=harvestgrid_ring_adapter --cov=harvestgrid_forecasting --cov=harvestgrid_notifications --cov=harvestgrid_api --cov-branch --cov-report=term-missing --cov-fail-under=90

test-contract:
	$(UV) run pytest tests/contract

test-security:
	$(UV) run pytest tests/security
	$(PIP_AUDIT) $(PIP_AUDIT_ARGS) --skip-editable

web-install:
	cd apps/web && corepack pnpm install --frozen-lockfile

web-dev:
	cd apps/web && corepack pnpm dev

# Exactly the bundle GitHub Pages serves: fixture store, /harvestgrid/ base path.
web-build-demo:
	cd apps/web && VITE_DEMO_MODE=true VITE_BASE_PATH=/harvestgrid/ corepack pnpm build

test-web:
	cd apps/web && corepack pnpm typecheck
	cd apps/web && corepack pnpm test
	cd apps/web && corepack pnpm build

build:
	$(UV) build

test-integration:
	@echo "BLOCKED: restricted storage, database, and documented Ring integration are not implemented." >&2
	@exit 1

test-e2e:
	@echo "BLOCKED: coordinator web E2E against a live API, an actual Ring device/simulator, and deployed storage are not implemented." >&2
	@exit 1

# The local stack is docker-compose (not CDK); this validates the file statically, because the
# sandbox used to author it has no container runtime. `docker compose config` is the real check.
test-infra:
	@docker compose version >/dev/null 2>&1 || { \
		echo "BLOCKED: no container runtime here. Validate with: docker compose config -q" >&2; \
		exit 1; }
	docker compose config -q
	@echo "compose file is valid; no CDK application exists (see docs/local-stack.md)."

deploy:
	@echo "BLOCKED: deployment is unavailable until infrastructure and live preflight exist." >&2
	@exit 1

evidence:
	@echo "Foundation only: no Ring, residential-media, forecast-evaluation, pilot, or release evidence. See docs/validation.md."

compose-up:
	docker compose up --build

compose-down:
	docker compose down

compose-logs:
	docker compose logs -f --tail=100

# Records docs/media/harvestgrid-demo.webm from a running demo server.
demo-record:
	cd apps/web && corepack pnpm exec playwright install chromium
	node --experimental-strip-types scripts/record-demo.ts

# Verifies the scene plan and the served page without needing a browser binary.
demo-selftest:
	node --experimental-strip-types scripts/record-demo.ts --selftest
