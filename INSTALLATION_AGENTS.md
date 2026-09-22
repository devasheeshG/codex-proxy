# Install Codex Proxy with an agent

Copy the prompt below into a coding or infrastructure agent that has terminal
access to the machine where you want to run Codex Proxy. The agent should do the
installation, but it must stop for the few browser and secret-handling steps
that require a human.

```text
Install Codex Proxy for me from:
https://github.com/devasheeshG/codex-proxy

Treat this as an infrastructure change. Work carefully, preserve anything that
already exists, and do not start making changes until you have interviewed me
and summarized the agreed topology.

Repository facts
----------------

- `docker-compose.yaml` contains the application services.
- `docker-compose.postgres.yml` adds the bundled PostgreSQL service.
- `docker-compose.traefik.yml` adds the bundled Traefik gateway.
- The dashboard is served at `/`, the admin and proxy API at `/api`, and the
  Codex-compatible provider base URL is `<origin>/api/v1`.
- `.env.example` is the authoritative configuration template.
- `scripts/blue-green.sh` and `make deploy-blue-green` are for updates to an
  already-running production deployment. Do not use blue-green deployment for
  the first local boot or as a substitute for understanding the target host.
- Never start a second Traefik instance on host ports already owned by another
  gateway. Blue and green are separate Compose projects but share one gateway.

Phase 1: interview me
---------------------

Ask these questions before changing the host. Combine them into a short,
readable checklist and explain any answer that has an operational consequence.

1. Is this a local/test installation or a production installation?
2. Which machine and absolute directory should contain the repository? Should
   you clone it there, or is there an existing checkout that must be preserved?
3. For production, what public domain will be used, and has its DNS already been
   pointed at this host? Should TLS be handled by the bundled Traefik, by an
   existing reverse proxy/load balancer, or upstream outside this machine?
4. Is Traefik already running on this host or Docker network? If so, ask for its
   Compose project/network and routing conventions. Do not publish another
   gateway on the same ports.
5. Which host HTTP and HTTPS ports may this installation use? Check that the
   requested ports are actually free before starting anything.
6. Should the stack create its bundled PostgreSQL 16 database, or connect to an
   existing PostgreSQL server? For an existing database, ask for host, port,
   database name, username, how its password will be supplied securely, whether
   it is reachable from Docker, and whether its backup/retention policy is
   already handled. Do not overwrite or initialize an unrelated database.
7. Should raw request/response archiving be disabled, or use an existing
   S3-compatible store? If enabled, ask for endpoint, region, bucket, optional
   prefix, path-style requirement, separate read credentials, whether archive
   writes must fail closed, hot-retention duration, and whether Borg retention
   is required. Explain that captures can contain prompts and tool data.
8. Are Telegram notifications wanted? If yes, explain that the bot token,
   destination, schedules, templates, and event choices are configured after
   boot in the dashboard. Do not ask me to paste the bot token into chat.
9. What admin username should be used? Ask whether I will set the admin password
   through a private local prompt or whether you should generate it directly
   into the protected `.env` file. Never print the password.
10. Confirm that Docker Engine with Compose v2 and Git are installed, and ask
    before installing system packages or changing the firewall, DNS, or an
    existing reverse proxy.

Do not silently choose between bundled and existing infrastructure. If an
existing reverse proxy requires a Compose override or shared Docker network,
show me the proposed integration before writing it. Keep host-specific override
files untracked and do not modify the application's routing semantics.

Phase 2: inspect and plan
-------------------------

After I answer:

1. Inspect the host, requested directory, listening ports, Docker networks,
   existing containers, and any existing checkout using read-only commands.
2. Clone the repository if it is absent. If it exists, inspect its branch,
   remotes, status, and deployment state; do not discard local changes.
3. Read `README.md`, `.env.example`, the selected Compose files, and, for an
   existing production deployment, `scripts/blue-green.sh` before acting.
4. Present the exact topology, selected Compose files, public URLs, persistent
   volumes/external services, and verification plan. Resolve conflicts first.

Phase 3: configure secrets safely
---------------------------------

1. Set a restrictive umask (`077`) before creating `.env`, and ensure `.env` is
   readable and writable only by its owner. It is gitignored and must never be
   committed.
2. Start from `.env.example`. Fill only values supported by that file; do not
   invent environment variables.
3. Generate a valid Fernet key, a cryptographically random JWT secret of at
   least 32 characters, and a strong PostgreSQL password when using the bundled
   database. Set the admin password by the private method agreed above.
4. Generate secrets inside a non-verbose local process that writes directly to
   `.env`. Do not echo them, include literal secret values in tool calls, command
   arguments, shell history, diffs, logs, summaries, screenshots, or chat. Do
   not enable shell tracing. Redact environment output.
5. When existing database, S3, or notification credentials are needed, arrange
   for me to enter them through a private local mechanism or protected file.
   Never request them in chat and never print them back for confirmation.
6. Leave `ARCHIVE_ENABLED=false` unless an S3-compatible destination has been
   explicitly configured and tested. If it is enabled, ensure bucket policy,
   encryption, retention, and read/write credentials match the decisions from
   the interview.

Phase 4: build and start
------------------------

Use the smallest correct Compose topology:

- Bundled PostgreSQL and bundled Traefik:
  `docker compose -f docker-compose.yaml -f docker-compose.postgres.yml -f docker-compose.traefik.yml up -d --build --wait`
- External PostgreSQL and bundled Traefik:
  omit `docker-compose.postgres.yml` and configure the `POSTGRES_*` values.
- Existing gateway:
  omit `docker-compose.traefik.yml` only after integrating the backend and
  frontend with that gateway through an approved, host-specific override. Route
  `/api` to backend port 80 and `/` to frontend port 3000 on a private Docker
  network; do not expose the database publicly.

For the first installation, render the final Compose configuration with
`docker compose ... config --quiet`, then build and start it normally. Confirm
that the one-shot `backend-init-db` migration completed successfully and that
the long-running backend, frontend, quota refresher, notification dispatcher,
and any selected infrastructure services are healthy.

For a later update to an existing production installation, do not use an
in-place `docker compose up --build`. Follow the repository deployment rules:

1. Run `scripts/blue-green.sh status`.
2. Render the blue/green Compose configuration exactly as documented.
3. Start `scripts/probe-availability.sh` against the public health endpoint for
   an independent one-second availability record.
4. Deploy with `scripts/blue-green.sh` or `make deploy-blue-green`.
5. Verify the promoted API and frontend before the old slot is drained.

Phase 5: verify the installation
--------------------------------

1. Verify container health and inspect only sanitized logs. Do not dump the
   environment or database connection strings.
2. Request `<origin>/api/health` with short connection and total timeouts and
   require a successful response.
3. Open the dashboard at `<origin>/`, confirm the login page loads, and have me
   sign in with the admin credentials. For production, verify the TLS
   certificate, hostname, and HTTPS redirect behavior.
4. Confirm that the database and any archive storage are persistent and not
   exposed on an unintended public port.
5. If a check fails, diagnose it without destructive resets. Never delete a
   volume, database, archive, or working tree to make an installation pass.

Phase 6: complete the human-authorized setup
--------------------------------------------

The following actions require me. Guide me through them one at a time; do not
pretend they were completed automatically.

1. In the dashboard's Accounts page, start **Add Codex account**. Have me open
   the official OpenAI device page, sign in to an account I control, enter the
   one-time code, and wait for the dashboard to finish the initial quota probe.
   Repeat only for accounts I am authorized to add. You must not handle my
   OpenAI password, cookies, or MFA.
2. In Users, create the first proxy user with the desired policy and priority.
   Create an API key only after entering a meaningful key label. The `usr_...`
   value is displayed once; do not copy it into chat or logs.
3. On the computer where Codex CLI runs, use the one-time **Quick setup** command
   displayed by the key dialog. On macOS/Linux it uses the served `install.sh`;
   on Windows it uses `install.ps1`. This installs the Codex Proxy provider and
   protected token helper while preserving unrelated Codex configuration and a
   backup of the previous config.
4. If I ask you to execute the helper locally, consume the key through a secure
   prompt or protected file. Never place the literal key in a recorded command.
   Confirm that the generated Codex key/config files have restrictive
   permissions.
5. Run a small Codex request through the proxy and confirm it appears in Events
   and usage accounting. Do not print the key, OAuth tokens, prompt contents, or
   archived response body during verification.
6. If requested, configure Telegram in Notifications through the dashboard,
   enter the bot token privately, send a test notification, and enable only the
   desired events. API fallback remains off for users unless explicitly enabled.
7. Ask whether I also want the optional macOS menu-bar app installed on a Mac.
   Only continue if I explicitly opt in and the agent has access to that Mac.
   Download the latest `Codex-Proxy-macOS.zip` from
   `https://github.com/devasheeshG/codex-proxy/releases`, unzip it into
   `/Applications`, and explain that the build is ad-hoc signed rather than
   notarized. If Gatekeeper blocks first launch, have me Control-click **Codex
   Proxy.app** and choose **Open**. Never ask for or handle my macOS password.

Final handoff
-------------

Give me a concise handoff containing:

- installation directory and checked-out revision;
- topology and selected Compose files;
- dashboard URL, API base URL, and health URL;
- healthy services and verification results;
- persistent volumes and external dependencies;
- where protected configuration lives, without showing secret values;
- which human steps are complete and which remain;
- exact update and rollback procedure appropriate to this installation.

Never claim success until health, login, one authorized Codex account, one
labelled user key, the Codex CLI helper, and one end-to-end request have been
verified, or clearly mark the unfinished items as requiring my action.

## Approved multi-egress IP setup

When the company has approved multiple provider egress IPs, ask for the AWS
region, instance/ENI, secondary private-IP and Elastic-IP mapping, and the
provider-approved public-IP list before changing infrastructure. Confirm that
the operator may allocate EIPs and that no existing address is being reused.

The supported topology is one EC2 ENI with multiple private addresses, each
mapped to an Elastic IP, plus one host-network relay Compose project. The host
boot service (`ops/aws-egress/recallr-egress-ips*`) restores secondary private
addresses after reboot. The relay has one local listener per target and binds
its upstream socket to that private address; the application never needs
privileged network access. Keep relay listeners private to the host and use a
long random token and a provider-only hostname allowlist.

For each account, the dashboard's **Egress network path** field lists the
enabled configured targets. An account with no assignment, or a cleared value,
is pinned to the first enabled target in `EGRESS_TARGETS_JSON`; there is no
automatic rotation or failover between addresses. An explicit target remains
on that account for inference, OAuth refresh, quota refresh, and warm-up. Do
not promise that an IP can be selected from an arbitrary client request: the
binding is an operator-controlled account setting.

Deployment order is: validate AWS mappings and the boot service; validate both
Compose files; start/health-check the relay project; deploy the app with
`scripts/blue-green.sh`/`make deploy-blue-green`; then verify the dashboard,
`/api/accounts/egress-targets`, and one sanitized provider probe per target.
Run `scripts/blue-green.sh status` and the independent availability probe
before every live update. Never create a second Traefik gateway, expose relay
ports to the Internet, print `.env`, or delete a production EIP to recover
from a failed rollout.
```
