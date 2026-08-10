# Local development

This note documents the local development behavior in the Codex Proxy. It is intentionally small so operators and contributors can find one decision without searching the entire README.

## Operational contract

- Keep the behavior observable in structured logs and the dashboard when it affects a live request.
- Preserve UTC storage, explicit error codes, and the existing authentication and redaction rules.
- Prefer the shared implementation and serializer so the overview, events, and API views cannot drift.

## Verification

Run the focused backend test for this area, then the full Codex backend suite and frontend checks before deployment. During a rollout, verify both the health endpoint and the promoted slot.

See the corresponding source module, tests, and deployment guide in this repository for the current implementation details.
