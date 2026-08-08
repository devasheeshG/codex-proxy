# Contributing

Thanks for your interest in improving Codex Proxy. Issues and pull requests are welcome.

## Issues

Use GitHub Issues for reproducible bugs, focused feature requests, and documentation
problems. Before opening one, search the existing issues and confirm the problem
still occurs on the latest `main` branch.

For a bug, include the affected version or commit, deployment method, reproduction
steps, expected behavior, actual behavior, and sanitized logs. Never post OAuth
tokens, API keys, cookies, archived prompts, database dumps, or other private data.

Keep one problem per issue. Operational support questions are welcome when they
can produce a documentation or code improvement, but maintainers do not guarantee
a response time. Duplicate, unactionable, or out-of-scope issues may be closed.

Security vulnerabilities do not belong in Issues. Follow `SECURITY.md` and use
GitHub's private vulnerability-reporting flow.

## Project layout

- `backend/` — FastAPI app (the proxy + admin API), Alembic migrations, and the runtime scripts under `app/scripts/`.
- `frontend/` — Next.js (App Router) admin dashboard.
- `docker-compose*.yml` — the main app services, plus the bundled Postgres and Traefik overlays.
- `frontend/public/` — non-destructive Codex profile installers for end users.

## Development setup

Prerequisites: Docker + Docker Compose, [uv](https://docs.astral.sh/uv/) (backend), and Node 22 + [pnpm](https://pnpm.io/) (frontend).

The fastest way to run the whole stack is Docker: `cp .env.example .env`, fill in the secrets (see the README quickstart), then `docker compose -f docker-compose.yaml -f docker-compose.postgres.yml -f docker-compose.traefik.yml up -d --build`.

For production deployment changes, use `scripts/blue-green.sh` as documented in
the README. It requires one existing Traefik gateway and performs health checks
before switching slots; use `scripts/blue-green.sh status` to inspect the active
generation without changing containers.

### Backend

```bash
cd backend
uv sync
uv run pytest        # tests (spins up an ephemeral Postgres via testcontainers)
make lint            # ruff check + ruff format --check
make format          # auto-fix formatting and lint
```

House conventions (enforced by lint/CI — please match them):

- Synchronous SQLAlchemy 2.0 (no async ORM). ORM models are named `*Db` with UUID primary keys.
- Use clear, idiomatic Python type hints consistent with the surrounding module.
- Routes are sync `def` (async only for the streaming proxy), use `PUT` not `PATCH`, carry `# Path:`/`# Description:` header comments and a `response_model`, and put `# noqa: B008` on every `Depends()`.
- Keep route logic inline rather than extracting single-use helpers.

### Frontend

```bash
cd frontend
pnpm install
pnpm lint
pnpm build
```

## Database migrations

The schema ships as a single baseline migration (`backend/alembic/versions/001_initial_migration.py`). For schema changes, edit the ORM models in `backend/app/utils/postgres/schemas.py` and add a new Alembic revision (`uv run alembic revision -m "..."`), keeping the upgrade/downgrade in sync with the models.

## Pull requests

Pull requests are accepted. A prior issue is not required for a small bug fix,
test, or documentation improvement. Open an issue before investing in a large
feature, schema redesign, protocol change, or architectural rewrite so scope can
be agreed first. Report security fixes privately before opening a public PR.

1. Branch off `main` and keep the change focused.
2. Add or update tests for behavior changes and include screenshots for visible UI changes.
3. Update documentation and migrations when the public behavior or schema changes.
4. Ensure `make lint` and `uv run pytest` pass for the backend, and `pnpm lint && pnpm build` pass for the frontend.
5. Explain the problem, approach, verification, compatibility impact, and any follow-up work in the PR description.
6. Open the PR; CI runs the same checks. A merge into `main` does not imply
   line-by-line human review; the separately maintained `human` branch is the
   human-reviewed line when available.

Draft PRs are welcome for early feedback. Maintainers may request changes, split
an oversized PR, or close work that is unsafe, out of scope, or cannot be
maintained. There is no contributor license agreement at present; contributions
are submitted under the repository's AGPL-3.0 license. All participation is
governed by `CODE_OF_CONDUCT.md`.
