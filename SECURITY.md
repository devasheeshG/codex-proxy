# Security Policy

## Reporting a vulnerability

Please report security issues privately — do **not** open a public GitHub issue
or pull request for a vulnerability. Use the repository's
[private vulnerability report](https://github.com/devasheeshG/codex-proxy/security/advisories/new)
with a description, reproduction steps, affected versions, and impact. The
maintainers will coordinate validation, remediation, and disclosure with the
reporter. Do not include real credentials or production data unless a maintainer
explicitly requests a secure transfer.

Repository maintainers must keep **Private vulnerability reporting** enabled in
GitHub's **Settings → Code security** page so this channel remains available.

## Threat model

This service brokers inference on a pool of real Codex subscription accounts, so a few secrets are the crown jewels:

- **Pooled-account OAuth tokens** — stored Fernet-encrypted at rest (`access_token_enc` / `refresh_token_enc`). A refresh token grants ongoing access to that Codex account.
- **`FERNET_KEY`** — decrypts those tokens. Anyone with it plus database access can recover every pooled account's credentials.
- **`JWT_SECRET`** — signs admin sessions. A known/guessable value lets anyone forge an admin token and take over the whole pool. The app refuses to boot with an empty, placeholder, or sub-32-character value.
- **`ADMIN_PASSWORD`** — the dashboard login; admin access is full control over accounts, users, and keys.
- **User API keys (`usr_…`)** — stored only as SHA-256 hashes; the plaintext is shown once at creation.

## Hardening checklist for operators

- Generate strong, unique `FERNET_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD`, and `POSTGRES_PASSWORD` (the startup guard rejects weak/placeholder admin secrets).
- Serve the dashboard and API over HTTPS (use the Traefik override or your own TLS-terminating proxy); don't expose the admin dashboard to the public internet unnecessarily.
- Set per-key rate limits and monthly token budgets to contain a leaked key.
- Rotate `FERNET_KEY` / `JWT_SECRET` if you suspect exposure (rotating `FERNET_KEY` requires re-adding accounts, since existing ciphertext can no longer be decrypted).
- Keep the host, Docker images, and dependencies updated.

## Supported versions

This is a young project; security fixes target the latest `main`.
