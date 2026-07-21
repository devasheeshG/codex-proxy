# Project Instructions

## Task tracking

- Always track tasks and keep their statuses current.
- Include an approximate ETA in every pending or in-progress task title, and update it as work progresses.
- When a task is finished, mark it completed and remove the ETA from its title.

## Deployment safety

- Use `scripts/blue-green.sh` / `make deploy-blue-green` for production changes.
- Keep one Traefik instance on the shared network. Blue and green are separate
  Compose projects; never publish a second gateway on the same host ports.
- The script waits for healthchecks, probes `/api/health` in the background,
  promotes and verifies the healthy backend before promoting the frontend, and
  drains the old slot only after both routes identify the new slot. Do not
  bypass it with an in-place `docker compose up --build` during a live update.
- Use `scripts/probe-availability.sh` for an independent one-second availability
  log with short connection and whole-request timeouts during live rollouts.
- Run `scripts/blue-green.sh status` and render Compose with
  `BG_SLOT=blue BG_PRIORITY=1 BG_API_PRIORITY=2 docker compose ... config --quiet`
  before changing deployment files.
