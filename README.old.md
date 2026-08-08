# Codex Proxy

A self-hosted gateway for pooling multiple Codex subscription accounts behind one
Codex CLI endpoint. It provides quota-aware account rotation, failover, encrypted
OAuth storage, per-user API keys, usage analytics, and a web dashboard.

```text
Codex CLI ── usr_ key ──► Codex Proxy ── pooled account OAuth ──► ChatGPT Codex
                                │
                                ├─ priority-ordered, quota-aware rotation
                                ├─ 401 refresh, capacity, and 429 failover
                                ├─ per-user/key limits and token ledger
                                └─► OpenAI-compatible API fallbacks (last resort)
```

> **Important:** only add accounts you control and are authorized to use this
> way. Pooling or sharing subscription access may be restricted by your OpenAI
> agreement. This independent project is not affiliated with OpenAI.

## Features

- Multiple Codex subscription accounts with priority-ordered routing. Every request starts at the highest-priority eligible account; accounts are never pinned to a user.
- Automatic OAuth refresh, quota-based rotation, demand-triggered account warm-up,
  cooldowns, and transparent failover before response bytes reach the client.
- Official Codex device authorization flow in the dashboard. Adding an account
  does not overwrite the login in your normal `~/.codex` directory.
- Live provider-reported Codex quota windows (5-hour, weekly, and/or monthly), reset times, and plan type. The dashboard only renders windows OpenAI returned for that account.
- Banked rate-limit reset credits can be inspected and explicitly redeemed from
  the Accounts UI. When a weekly provider window is exhausted, the proxy
  automatically redeems the eligible credit expiring first and retries the
  interrupted request. Redemption is sent to OpenAI; the proxy never fakes a
  local quota reset.
- Per-user monthly and lifetime token/spend budgets, per-key request/token
  limits, and revocation.
- Per-user model allowlists and exact server-side model rewrites, applied before
  provider selection without changing another user's routing.
- Responses API streaming and non-streaming usage capture, including cached
  input tokens.
- OpenAI Chat Completions compatibility at `/api/v1/chat/completions`, translated
  to the upstream Responses protocol for text, image/file inputs, function tools,
  structured output, reasoning, streaming, and usage reporting.
- OAuth tokens encrypted with Fernet; user API keys stored only as hashes.
- Multiple OpenAI-compatible API fallbacks with independent base URLs,
  priorities, encrypted write-only credentials, health checks, enable/disable
  controls, and monthly spend caps.
- Next.js dashboard, FastAPI backend, PostgreSQL, and composable Docker deploy.
- Linux/macOS and Windows installers that update the native Codex config in
  place without installing another Codex launcher.

The provider quota model follows OpenAI's documented shared
[five-hour Codex window with additional weekly limits](https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan).
When a plan does not return a weekly window, the dashboard reports it as
unknown instead of displaying a misleading 0%.

## Quick start

Prerequisites: Docker and Docker Compose.

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated values in `FERNET_KEY` and `JWT_SECRET`, then set strong
`ADMIN_PASSWORD` and `POSTGRES_PASSWORD` values.

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.postgres.yml \
  -f docker-compose.traefik.yml \
  up -d --build
```

Open `http://localhost:8080` and sign in with `ADMIN_USERNAME` and
`ADMIN_PASSWORD`. The API is served from the same origin at `/api`.

The Compose files are split by responsibility:

| File | Services |
| --- | --- |
| `docker-compose.yaml` | Backend, database migration, quota refresher, notification dispatcher, dashboard |
| `docker-compose.postgres.yml` | Bundled persistent PostgreSQL |
| `docker-compose.traefik.yml` | Single-origin HTTP/HTTPS entry point |

Use the same `-f` arguments for `logs`, `down`, and other Compose commands. Omit
the PostgreSQL overlay when using an external database.

## Add accounts

In **Accounts**, select **Add account**:

1. The proxy starts Codex's device authorization flow.
2. Open the displayed OpenAI page and enter the displayed code.
3. Approve the account, return to the dashboard, and select **Complete login**.

Repeat for every account in the pool. Each card exposes its quota windows,
rotation settings, refresh/reauthentication controls, and available banked limit
resets.

The **Users** page shows each user's all-time token and API-equivalent spend
totals plus current calendar-month usage, configured monthly/lifetime budgets,
and the UTC reset timestamp for the monthly window. Account cards show the same
all-time and month-to-date spend attribution for traffic routed through each
subscription.

Team/Business account cards also show `Team: <workspace name>`. OpenAI's
documented Codex account response currently exposes the account email and plan,
but not the workspace display name, so the name can be set from **Edit** on any
member of that workspace. The proxy reuses it across every linked member and
will automatically capture it if OAuth supplies a recognized workspace-name
claim in the future.

Account uniqueness is workspace-aware. The same email may be linked once to a
Personal workspace and once to each distinct Team workspace, and different
members may share one Team workspace. The same ChatGPT member cannot be added
twice to the same workspace; duplicate adds and duplicate-producing
re-authentication attempts return HTTP `409`.

Manual redemption is a confirmation flow because it consumes a real reset
entitlement on the selected OpenAI account and may not be reversible. A reset
can be manually redeemed when weekly Codex usage is above 90%, or when that
specific reset expires within 12 hours. Independently, a fully exhausted weekly
window triggers automatic redemption of the available, plan-supported,
unexpired credit with the earliest expiration. The redemption uses an
idempotency key and the failed request is retried once. Five-hour and monthly
usage do not trigger automatic redemption.

## Add API fallbacks

Open **API fallbacks** to connect one or more pay-as-you-go credentials. Every
entry has its own label, OpenAI-compatible base URL (the default is
`https://api.openai.com/v1`), priority, optional monthly USD spend cap, provider
health, and enable/disable state. The full API key is encrypted immediately and
is write-only: after saving, the backend and dashboard return only a short
masked suffix. Replacing a key is explicit; leaving the key field blank while
editing preserves the stored secret.

Subscription accounts are always tried first. Fallback priority applies only
after no eligible subscription can serve the request. A failed or exhausted
fallback transparently advances to the next eligible entry. `401` marks a key
as rejected, `429`/server failures put the entry into a temporary cooldown, and
**Test & refresh models** validates the credential without generating inference
traffic. Disabled, rejected, cooled-down, and over-budget entries never route.

Month-to-date spend is metered from the token usage returned by successful
Responses calls and the model prices in `backend/app/pricing.py`; its window
resets at the next UTC calendar month. Because cost is known only after a
response completes, the single request that crosses a cap may exceed it
slightly, after which routing stops using that entry. Models absent from the
local pricing table report zero estimated spend, so operators using a custom
compatible provider must keep that table aligned with their provider's prices.

## Add users and configure Codex

In **Users**, create a user and generate a `usr_…` key. The plaintext key appears
once. The setup dialog generates a ready-to-run installer command.

The installer:

- updates `~/.codex/config.toml` in place and keeps a `.bak` copy;
- stores the proxy key in a mode-`0600` file;
- creates a small bearer-token helper;
- sets the proxy as the active provider while preserving unrelated settings;
- removes launchers created by older versions of this installer;
- leaves `~/.codex/auth.json`, the Codex binary, and running processes untouched.

The resulting native config uses the Responses wire API:

```toml
model_provider = "codex_proxy"

[model_providers.codex_proxy]
name = "OpenAI"
base_url = "http://localhost:8080/api/v1"
wire_api = "responses"

[model_providers.codex_proxy.auth]
command = "/home/example/.codex/proxy-token"
refresh_interval_ms = 0
```

New `codex` sessions use the pool through the original Codex CLI installation.

Each user can independently allow Standard, Fast, and access-controlled
UltraFast request modes, thinking levels, and models. UltraFast passes
`service_tier: ultrafast` through to the upstream and is currently an upstream
entitlement for `gpt-5.6-sol`; allowing it here does not grant that entitlement.
Model access defaults to unrestricted, including models introduced later.
Switching a user to a model allowlist filters `GET /api/v1/models` for
that user's keys and rejects any omitted or disallowed model with `403` before
the request reaches an upstream account. Opening the Users page automatically
refreshes every eligible subscription and API fallback model catalog, then
builds the searchable model list from those catalogs, observed usage, and
existing policies. A failed provider refresh does not hide cached models or
models returned by other providers; an exact model ID can also be added manually.
Per-user model rewrites are exact, case-normalized mappings. The allowlist is
checked against the model the client requested, then the mapped upstream model
is used for account capability selection, fallback routing, and usage pricing.

## Rotation behavior

For every `/api/v1/responses` or `/api/v1/chat/completions` request the backend:

1. authenticates the `usr_` key and checks user/key budgets;
2. starts at the highest-priority eligible account (there is no per-user account pinning);
3. skips disabled, cooled-down, or over-threshold accounts;
4. refreshes an OAuth token when needed (refresh-token rotation is serialized
   per account; a 401 forces a locked refresh without writing a stale expiry);
5. injects the selected account bearer token and ChatGPT account ID;
6. retries a revoked credential once after refresh;
7. places an account reporting model capacity into a 60-second temporary cooldown and immediately fails over; cools down and fails over on quota exhaustion or `429`;
8. tries enabled, healthy, under-budget API fallbacks by priority only when the
   subscription pool cannot serve;
9. relays the response and records final Responses API usage and fallback spend.

An account-scoped empty-body `404` from the ChatGPT Codex backend is treated
as a transient provider failure: the account is marked degraded, placed in its
configured cooldown, and excluded while the same request immediately tries the
next pooled account. Non-empty `404` responses remain ordinary upstream client
responses and are not reclassified.

### Upstream capacity failover

Capacity responses such as “Selected model is at capacity”, “try a different
model”, “service exhausted”, or temporary provider overload are handled before
any response bytes reach the client. The account is placed into a 60-second
temporary cooldown and the next eligible account is tried immediately. The
cooldown prevents a thundering herd while leaving the account available again
for a later request. The same behavior is applied to configured API fallbacks.

The five-hour rotation threshold, weekly rotation threshold, cooldown duration,
and maximum attempts are configured independently per account in the dashboard.
When accounts share a priority, routing prefers the earliest known weekly reset;
if weekly reset data is unavailable or tied, it prefers the earliest known
five-hour reset, then falls back to a deterministic account order.
Editing an account's priority changes only that account, so multiple accounts
may intentionally share a priority level.

### Account-window warm-up

Warm-up stays completely idle until successful real traffic consumes the
configured share of the eligible pool's aggregate five-hour capacity (10% by
default). The backend then schedules one background batch that sends a minimal
internal Responses request to every other eligible account whose five-hour
window has not started. An advisory lock prevents concurrent requests from
starting duplicate batches.

Disabled, reauthentication-required, cooled-down, monthly-only, already-warm,
and weekly-reserve accounts are skipped. Administrators can also use the
**Warm Up** button on an account card to start one eligible cold account
immediately. Warm-up requests are not attributed to an API user or key, but
they do consume a small amount of provider capacity.

## Telegram notifications

Open **Notifications** in the dashboard to connect a Telegram bot. The bot
token is encrypted with `FERNET_KEY` and is never returned to the browser after
it is saved. Configure the destination group/chat ID and, for forum groups, an
optional topic ID. Set the **Timezone** field to the IANA timezone that should
define report boundaries and delivery timing (the default is `Asia/Kolkata`,
Indian Standard Time), then use **Send test** to verify delivery.

Every account, pool, user, and API-key event can be enabled independently. Its
plain-text message template and repeat cooldown are editable in the same page.
Messages are written to a persistent outbox and delivered by the
`notification-dispatcher` service, so Telegram latency or downtime never blocks
proxy traffic. Account and pool signals are enabled by default; noisy user/key
guardrail signals start muted.

The connected bot also supports on-demand account status queries in the saved
chat (and only in the saved forum topic, when one is configured):

```text
/codex status usable
/codex status available
/codex status all
```

`usable` shows healthy accounts that can serve traffic immediately. `available`
matches the dashboard's active pool: enabled, authenticated accounts, including
accounts that are temporarily unusable because of quota or degraded
health. `all` also includes disabled and reauthentication-required accounts. Each
entry reports the account label and email, remaining five-hour and weekly
usage, both provider reset times, both rotation thresholds, and current
eligibility status in the notification timezone. The command defaults to
`usable` when the argument is
omitted. Accounts with a banked reset credit also show `Limit reset credit:
Available`; that line is omitted when no credit is available. Give each
independently deployed proxy its
own bot token: Telegram permits only one reliable `getUpdates` consumer per bot,
so sharing a token between proxy deployments can make command updates race.

The dashboard's **Authenticated accounts** filter normally excludes accounts
whose provider health is `REAUTH_REQUIRED`. An account can be explicitly marked
**Show in Authenticated accounts** from its Edit dialog to keep it visible while
you investigate or re-authenticate it. This presentation-only override never
changes provider health, usable/available eligibility, or request routing.
Expired 429 cooldowns are normalized back to Active automatically when accounts
are listed, so stale cooldown labels cannot hide an eligible account.

The available events are:

| Event | When it is sent | Default | Repeat cooldown |
| --- | --- | --- | --- |
| **Telegram notifier connected** | Once, when a Telegram token and destination are saved and the channel is enabled for the first time. Re-enabling it later does not resend the event. | On | Once |
| **Pooled Codex account added** | After a new Codex subscription account is saved and its first quota probe completes. Includes the account label, email, plan, usage, and pool capacity by default. | On | Once per account |
| **Account authentication expired** | When stored OAuth credentials are rejected or unreadable and the account first enters reauthentication-required state. A normal access-token expiry that refreshes successfully does not trigger it. | On | Once per authentication cycle |
| **Account rotation threshold reached** | Once per limiting five-hour or weekly quota window when that window reaches its own configured rotation threshold and the account is removed from selection. | On | Once per limiting window |
| **Provider hard usage limit reached** | Once per limiting five-hour or weekly quota window when Codex reports exhausted usage, credits, or a workspace limit and traffic fails over. | On | Once per limiting window |
| **Entire account pool unavailable** | When an incoming request has no account available and is about to receive `503`. It only repeats after the cooldown when another request arrives. | On | 15 minutes |
| **User request rate limit exceeded** | When the user's combined API-key traffic exceeds its requests-per-minute limit and the request receives `429`. | Off | 15 minutes |
| **User monthly token budget exhausted** | When the user's month-to-date usage reaches its configured token budget and the next request receives `403`. | Off | 60 minutes |
| **API key request rate limit exceeded** | When one API key exceeds its own requests-per-minute limit and receives `429`; the user's other keys are unaffected. | Off | 15 minutes |
| **API key monthly token budget exhausted** | When one API key reaches its month-to-date token budget and receives `403`; the user's other keys remain available. | Off | 60 minutes |
| **Daily usage report** | After the previous local calendar day completes. Includes total requests, total tokens, and every active user (a user with usage in that period) with request/token shares. | On | Once per day |
| **Weekly usage report** | After the previous Monday–Sunday local reporting week completes. Includes the same totals and active-user distribution. | On | Once per week |
| **Monthly usage report** | After the previous local calendar month completes. Includes the same totals and active-user distribution. | On | Once per month |

Each event's template, enabled state, and cooldown can be customized independently.
The authentication-expired template exposes `account_label`, `account_email`,
`account_tier`, `authentication_code`, `authentication_reason`, `event_time`,
and `dashboard_url`. It becomes eligible again only after the account
successfully authenticates and later returns to reauthentication-required state.
Account templates expose explicit `five_hour_usage_percent`,
`five_hour_reset_at`, `weekly_usage_percent`, `weekly_reset_at`, and
`limiting_window` variables. The older `usage_percent` and `reset_at` aliases
remain available for customized templates and resolve to the currently limiting
or most-utilized window.
Report templates expose `period_start`, `period_end`, `generated_at`,
`total_requests`, `total_tokens`, `user_count`, and `user_distribution`.

## Configuration

See `.env.example` for the complete annotated list.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HTTP_PORT` | `8080` | Bundled Traefik HTTP port |
| `HTTPS_PORT` | `8443` | Bundled Traefik HTTPS port |
| `DOMAIN` | `localhost` | Public host and CORS origin |
| `POSTGRES_*` | — | PostgreSQL connection |
| `FERNET_KEY` | — | Required token-encryption key |
| `JWT_SECRET` | — | Required admin-session signing key |
| `ADMIN_USERNAME` | `admin` | Dashboard username |
| `ADMIN_PASSWORD` | — | Required dashboard password |
| `QUOTA_REFRESH_INTERVAL_SECONDS` | `60` | Background quota probe interval |
| `WARMUP_ENABLED` | `true` | Enable demand-triggered and manual account warm-up |
| `WARMUP_TRIGGER_POOL_USAGE_PCT` | `0.10` | Aggregate eligible five-hour pool usage that starts a batch |
| `WARMUP_WEEKLY_RESERVE_PCT` | `0.90` | Stop synthetic traffic at this weekly utilization |
| `WARMUP_MODEL` | `gpt-5.6-luna` | Minimal model used for warm-up requests |
| `DEFAULT_KEY_RATE_LIMIT_PER_MINUTE` | `0` | Default per-key request limit; `0` disables it |
| `CODEX_CLIENT_VERSION` | `0.144.5` | Version supplied to the Codex model catalog when clients omit it |
| `ARCHIVE_ENABLED` | `false` | Capture exact inference request/response bodies in S3-compatible storage |
| `ARCHIVE_REQUIRED` | `true` | Return/terminate on archive failure instead of silently losing a capture |
| `ARCHIVE_S3_*` | — | S3/MinIO endpoint, bucket, credentials, and retry settings |
| `ARCHIVE_S3_READ_*` | — | Optional read-only credentials used by the admin request explorer |
| `TRAEFIK_ENTRYPOINT` | `web` | Set to `websecure` for production TLS |
| `ACME_EMAIL` | — | Let's Encrypt contact for production TLS |

For HTTPS, point `DOMAIN` at the host, set `TRAEFIK_ENTRYPOINT=websecure` and
`ACME_EMAIL`, bind ports 80/443, and use the same Compose command.

## Zero-downtime Blue-Green deployment

Production updates should use `scripts/blue-green.sh` (or
`make deploy-blue-green`) when a single Traefik instance already fronts the
shared Docker network. The script starts the next generation in a separate
Compose project (`codex-proxy-blue` or `codex-proxy-green`), waits for every
healthcheck, promotes its Traefik routers, probes `/api/health` continuously in
the background, and then drains the old generation. The API router always has a
higher priority than the dashboard router; the script also increases the slot
priority on every promotion so routers from two generations never tie.

For the operator-specific external Postgres/Traefik setup:

```bash
# Run from this checkout; do not include a second Traefik service.
BG_COMPOSE_FILES=docker-compose.yaml:docker-compose.live.yml \
BG_URL=https://codex-proxy.example.com \
make deploy-blue-green

scripts/blue-green.sh status
```

The first run treats the existing `codex-proxy` Compose project as the legacy
active generation and creates blue. Later runs alternate blue and green. A
successful run writes `.blue-green-state` (ignored by git), while an interrupted
run removes the target project so stale routers cannot compete with the active
one. Set `BG_KEEP_OLD=1` to retain the old slot for a manual rollback; otherwise
the HTTP services and singleton workers are stopped after `BG_DRAIN_SECONDS`.

Routing uses Docker labels only. No deployment-generated Traefik files or
container IPs are written: Docker/Traefik discovers each slot by its Compose
labels, and the API priority is always higher than the dashboard priority.

To independently record availability during a rollout, run the short-timeout
probe in the background before deploying. It checks once per second, treats
non-2xx responses and curl errors as failures, and records each request:

```bash
scripts/probe-availability.sh \
  https://codex-proxy.example.com/api/health \
  /tmp/codex-proxy-availability.tsv &
probe_pid=$!
make deploy-blue-green
kill "$probe_pid"
wait "$probe_pid" || true
awk -F '\t' '$2 == "fail"' /tmp/codex-proxy-availability.tsv
```

The defaults are a `0.4` second connection timeout and `0.8` second total
request timeout. Override them with `PROBE_CONNECT_TIMEOUT_SECONDS` and
`PROBE_MAX_TIME_SECONDS` when needed. Before the old slot is drained, the
deployment script also requires three consecutive `/api/health` responses whose
`X-Deployment-Slot` header identifies the newly promoted slot. It promotes and
verifies the backend first, then promotes and verifies the frontend. This order
prevents a new frontend router from outranking the still-active API router while
the new backend is restarting.
The background probe reports any handoff failures; an interrupted run removes
the target project so stale routers cannot remain active.

For a host using the bundled gateway, start Traefik once and exclude it from the
slot service list. Blue-green slots must use an external/shared PostgreSQL
instance; do not try to start the fixed-name bundled Postgres in both slots:

```bash
docker compose -p codex-proxy-gateway \
  -f docker-compose.yaml -f docker-compose.traefik.yml \
  up -d traefik
BG_COMPOSE_FILES=docker-compose.yaml:docker-compose.traefik.yml \
BG_SERVICES='backend-init-db backend quota-refresher notification-dispatcher frontend' \
BG_URL=http://localhost:8080 make deploy-blue-green
```

Never run two Traefik gateways for the same published ports. Before making an
update, validate the rendered labels without changing containers:

```bash
BG_SLOT=blue BG_PRIORITY=1 BG_API_PRIORITY=2 \
  docker compose -f docker-compose.yaml -f docker-compose.live.yml config --quiet
```

When `ARCHIVE_ENABLED=true`, the proxy writes the exact client-facing request
and response bytes for inference routes directly to the configured
S3-compatible bucket. Each interaction receives a full UUID event ID and a
date-partitioned prefix containing exactly two gzip-compressed objects:
`request.json.gz` and `response.json.gz`. For streaming responses, the proxy
forwards each chunk immediately while buffering only its private archive copy;
the stored response bytes remain the exact SSE body.
Authorization and cookie headers are never copied, but prompt and tool content
may still contain secrets or personal data; restrict bucket access and apply
your retention/deletion policy.

New accounts use independent 100% (`1.0`) five-hour and weekly rotation
thresholds by default. Existing accounts inherit their previous single
threshold for both new windows during schema reconciliation.

The complete schema is defined by the single canonical Alembic revision `001`
in `001_initial_migration.py`. Production databases created from the former
history are stamped to this schema-equivalent head without replaying migrations
or changing application data. The database-init service performs this guarded
transition automatically from known equivalent former heads (`0009` and
`002`–`006`);
unknown or older revision states fail closed instead of being stamped blindly.
After upgrading, it also reconciles explicitly supported additive tables and
columns—including API fallback credentials, their usage attribution, and the
nullable per-user model allowlist—so databases already stamped at `001` remain
synchronized with the canonical schema without rewriting user data.

## API and architecture

- Codex inference: `POST /api/v1/responses`.
- OpenAI-compatible chat inference: `POST /api/v1/chat/completions`. Requests
  are translated to Responses upstream and responses are translated back to the
  Chat Completions schema. Both streaming and non-streaming modes are supported;
  `stream_options.include_usage` is honored. Function tools (including legacy
  `functions`), tool results, remote/data-URL images, files, JSON mode/schema,
  reasoning effort, and service tier are supported. When a Chat Completions or
  Responses request omits reasoning effort, the proxy explicitly uses `none`.
- Codex compaction relay: `POST /api/v1/responses/compact` (availability still
  depends on the selected ChatGPT subscription account).
- OpenAI model catalog: `GET /api/v1/models`.
- Admin API: `/api/v1/auth`, `/accounts`, `/fallbacks`, `/users`, `/stats`, and
  `/notifications`.
- Key-holder usage: `GET /api/v1/me/usage`, including average five-hour and weekly usage across currently available accounts, known/unknown measurement counts, every account's reset timestamp, the earliest upcoming reset, and per-account details ordered by routing priority.
- API documentation: `/api/docs`.

### OpenAI client compatibility

Use `https://your-domain.example/api/v1` as the base URL for OpenAI SDKs. The
proxy supports model listing plus streaming and non-streaming Responses API
creation. It accepts the standard string-input shorthand and translates the
Codex stream-only upstream protocol into a complete non-streaming Response.

The Chat Completions compatibility route is deliberately fail-closed for
semantics the subscription backend cannot represent: `n` values other than `1`,
stop sequences, log probabilities, audio input/output, and non-function tools.

The proxy does not implement Embeddings, Ollama routes, stored-response
retrieval/deletion/cancellation, or
`POST /responses/input_tokens`. The ChatGPT subscription backend currently
returns `404` for exact input-token counting; the public OpenAI API having that
operation does not make it available to subscription OAuth accounts. All other
Responses subpaths are rejected locally instead of being forwarded. Clients
must use the explicitly documented operations above.

For a Bifrost custom OpenAI provider, use `https://your-domain.example/api` as
the base URL because Bifrost appends `/v1`. Enable `list_models`, `responses`,
`responses_stream`, and `chat_completions`; leave unsupported request families
disabled.

Repository layout:

- `backend/` — FastAPI, SQLAlchemy, Alembic, subscription rotation, encrypted
  API fallbacks, OAuth, quota/reset integration, proxy, and tests.
- `frontend/` — Next.js admin dashboard and native-config installers.
- `docker-compose*.yml` — application, PostgreSQL, and Traefik stacks.

The dashboard's dollar figure is an API-equivalent estimate based on the
hard-coded standard OpenAI API table in `backend/app/pricing.py`; subscription
traffic is not billed by this project. The same table meters traffic sent
through pay-as-you-go fallbacks for proxy-side spend caps.

## Development

```bash
# Run the complete backend and frontend verification suite.
make verify

# In an operator checkout with docker-compose.live.yml configured:
make deploy          # rebuild, deploy, and wait for every service in one command
make deploy-frontend # rebuild only the dashboard frontend

cd backend
uv sync
make lint
uv run pytest

cd ../frontend
pnpm install
pnpm lint
pnpm format:check
pnpm build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

[AGPL-3.0](LICENSE)

