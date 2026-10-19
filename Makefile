# Build pipeline: TypeScript agent -> per-platform Bun executables -> Python wheels -> app.
.PHONY: check test agent wheels requirements dev clean

check:  ## lint and type-check both halves
	cd agent && pnpm run check
	uv run ruff format --check app python wheel scripts tests
	uv run ruff check .
	uv run ty check app python wheel scripts tests

test: wheels  ## unit and integration tests; set BRIEFING_LIVE=1 for live GitHub tests
	cd agent && pnpm test
	uv sync --reinstall-package pi-briefing
	uv run pytest -q

agent:  ## compile the agent for every platform into agent/dist
	cd agent && pnpm install --frozen-lockfile && bun run build.ts

wheels: agent  ## package the executables as wheels in dist/wheels
	uv run --no-project python wheel/build_wheels.py

requirements:  ## app/requirements.txt for Connect Cloud; needs WHEEL_BASE_URL
	uv run --no-project python scripts/write_requirements.py --wheel-base-url "$(WHEEL_BASE_URL)"

dev: wheels  ## run the app locally
	uv sync --reinstall-package pi-briefing
	uv run shiny run --reload app/app.py

clean:  ## remove build output (recoverable from the Trash)
	trash agent/dist dist 2>/dev/null || true
