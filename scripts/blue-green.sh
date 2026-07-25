#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/blue-green.sh [deploy|status]

Deploys a second Compose project and switches Traefik using Docker labels.
  BG_COMPOSE_FILES   Compose files separated by ':' (default: docker-compose.yaml:docker-compose.live.yml)
  BG_PROJECT_PREFIX  Compose project prefix (default: directory name)
  BG_URL             Public origin to probe (default derived from DOMAIN/ports)
  BG_WAIT_SECONDS    Maximum health wait (default: 180)
  BG_DRAIN_SECONDS   Grace period before stopping old slot (default: 10)
  BG_PROBE_CONNECT_TIMEOUT Connection timeout for public probes (default: 0.4)
  BG_PROBE_MAX_TIME  Whole-request timeout for public probes (default: 0.8)
  BG_PROMOTION_SUCCESSES Consecutive responses required from new slot (default: 3)
  BG_STATE_FILE      State file (default: .blue-green-state)
  BG_SERVICES        Space-separated services to start (default: all app services)
  BG_VALIDATE_ONLY=1 Validate slot label priorities without changing containers
  BG_KEEP_OLD=1      Leave the previous slot running for manual rollback
EOF
}

command_name="${1:-deploy}"
case "$command_name" in
  deploy|status) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root_dir"
env_file_value() { local key="$1"; [[ -f .env ]] || return 0; awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env; }
project_default=$(basename "$root_dir" | tr '[:upper:]_' '[:lower:]-')
project_prefix="${BG_PROJECT_PREFIX:-$project_default}"
state_file="${BG_STATE_FILE:-$root_dir/.blue-green-state}"
wait_seconds="${BG_WAIT_SECONDS:-180}"
drain_seconds="${BG_DRAIN_SECONDS:-10}"
probe_connect_timeout="${BG_PROBE_CONNECT_TIMEOUT:-0.4}"
probe_max_time="${BG_PROBE_MAX_TIME:-0.8}"
promotion_successes="${BG_PROMOTION_SUCCESSES:-3}"
compose_files="${BG_COMPOSE_FILES:-docker-compose.yaml:docker-compose.live.yml}"
deploy_services="${BG_SERVICES:-backend-init-db backend quota-refresher notification-dispatcher archive-retention frontend}"
domain="${DOMAIN:-$(env_file_value DOMAIN)}"; domain="${domain:-localhost}"
entrypoint="${TRAEFIK_ENTRYPOINT:-$(env_file_value TRAEFIK_ENTRYPOINT)}"
http_port="${HTTP_PORT:-$(env_file_value HTTP_PORT)}"; http_port="${http_port:-8080}"
compose_args=(); IFS=: read -r -a compose_file_list <<< "$compose_files"
for compose_file in "${compose_file_list[@]}"; do [[ -n "$compose_file" ]] || continue; [[ -f "$compose_file" ]] || { echo "Compose file not found: $compose_file" >&2; exit 1; }; compose_args+=( -f "$compose_file" ); done
compose() { local project="$1"; shift; docker compose -p "$project" "${compose_args[@]}" "$@"; }
validate_priorities() {
  local project="$1" slot="$2" priority="$3" api_priority="$4" rendered api_line frontend_line
  (( api_priority > priority )) || { echo "API priority must be greater than frontend priority." >&2; return 1; }
  rendered=$(BG_SLOT="$slot" BG_PRIORITY="$priority" BG_API_PRIORITY="$api_priority" compose "$project" config)
  api_line=$(grep -E 'routers\.[^:]*-api-'"$slot"'\.priority:' <<< "$rendered" || true)
  frontend_line=$(grep -E 'routers\.[^:]*-'"$slot"'\.priority:' <<< "$rendered" | grep -v -- '-api-' | head -1 || true)
  [[ "$api_line" == *"\"$api_priority\""* ]] || { echo "Rendered API router priority is not $api_priority for $slot." >&2; return 1; }
  [[ "$frontend_line" == *"\"$priority\""* ]] || { echo "Rendered frontend router priority is not $priority for $slot." >&2; return 1; }
}
read_state() { active_slot=legacy; active_project="$project_prefix"; active_priority=100; [[ -f "$state_file" ]] || return 0; while IFS='=' read -r key value; do case "$key" in active_slot) active_slot="$value";; active_project) active_project="$value";; active_priority) active_priority="$value";; esac; done < "$state_file"; }
write_state() { local tmp; tmp=$(mktemp "${state_file}.XXXXXX"); printf 'active_slot=%s\nactive_project=%s\nactive_priority=%s\nupdated_at=%s\n' "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp"; mv -f "$tmp" "$state_file"; }
if [[ "$command_name" == status ]]; then read_state; printf 'active slot: %s\nactive project: %s\n' "$active_slot" "$active_project"; for slot in blue green; do compose "$project_prefix-$slot" ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null || true; done; exit 0; fi
exec 9>"${state_file}.lock"; flock -n 9 || { echo "Another blue-green deployment is already running." >&2; exit 1; }
read_state; if [[ "$active_slot" == blue ]]; then target_slot=green; elif [[ "$active_slot" == green ]]; then target_slot=blue; else target_slot=blue; fi; target_project="$project_prefix-$target_slot"; promote_priority=$((active_priority + 100))
if [[ "${BG_VALIDATE_ONLY:-0}" == 1 ]]; then validate_priorities "$target_project" "$target_slot" 1 2; validate_priorities "$target_project" "$target_slot" "$promote_priority" $((promote_priority + 1)); echo "Blue-green Compose labels are valid for $target_slot (standby 1/2, promotion $promote_priority/$((promote_priority + 1)))."; exit 0; fi
if [[ "$entrypoint" == websecure || "${HTTPS_PORT:-}" == 443 ]]; then default_url="https://$domain"; else default_url="http://localhost:$http_port"; fi
public_origin="${BG_URL:-$default_url}"; probe_url="$public_origin/api/health"; frontend_url="$public_origin/"; probe_log=$(mktemp "${TMPDIR:-/tmp}/blue-green-probe.XXXXXX"); probe_pid=""; deployment_succeeded=0
cleanup() { if [[ -n "$probe_pid" ]]; then kill "$probe_pid" 2>/dev/null || true; wait "$probe_pid" 2>/dev/null || true; fi; if (( ! deployment_succeeded )); then echo "Deployment did not complete; removing standby project $target_project." >&2; compose "$target_project" down --remove-orphans >/dev/null 2>&1 || true; fi; echo "Probe report: $probe_log"; }
trap cleanup EXIT
probe_loop() { local total=0 failures=0 seen=0 code; : > "$probe_log"; while :; do code=$(curl -kfsS --connect-timeout "$probe_connect_timeout" --max-time "$probe_max_time" -o /dev/null -w '%{http_code}' "$probe_url" 2>/dev/null || true); if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then seen=1; elif (( seen )); then failures=$((failures + 1)); fi; total=$((total + 1)); printf 'requests=%d failures=%d last_status=%s\n' "$total" "$failures" "$code" > "$probe_log"; sleep "${BG_PROBE_INTERVAL:-1}"; done; }
wait_for_target_slot() {
  local url="$1" deadline=$((SECONDS + wait_seconds)) consecutive=0 headers received_slot
  while (( SECONDS < deadline )); do
    headers=$(curl -kfsS --connect-timeout "$probe_connect_timeout" --max-time "$probe_max_time" -D - -o /dev/null "$url" 2>/dev/null || true)
    received_slot=$(awk -F': *' 'tolower($1) == "x-deployment-slot" { gsub("\\r", "", $2); print $2; exit }' <<< "$headers")
    if [[ "$received_slot" == "$target_slot" ]]; then
      consecutive=$((consecutive + 1))
      if (( consecutive >= promotion_successes )); then return 0; fi
    else
      consecutive=0
    fi
    sleep 1
  done
  echo "Promoted slot $target_slot did not answer $url for $promotion_successes consecutive probes." >&2
  return 1
}
echo "Blue-green deploy: $active_project ($active_slot) -> $target_project ($target_slot)"; echo "Public probe: $probe_url"; probe_loop & probe_pid=$!
echo "Building and starting $target_project with standby priority..."; BG_SLOT="$target_slot" BG_PRIORITY=1 BG_API_PRIORITY=2 compose "$target_project" config --quiet; validate_priorities "$target_project" "$target_slot" 1 2; read -r -a deploy_service_list <<< "$deploy_services"; BG_SLOT="$target_slot" BG_PRIORITY=1 BG_API_PRIORITY=2 compose "$target_project" up -d --build --remove-orphans --wait --wait-timeout "$wait_seconds" "${deploy_service_list[@]}"
echo "Promoting $target_slot API after container health checks..."; validate_priorities "$target_project" "$target_slot" "$promote_priority" $((promote_priority + 1)); BG_SLOT="$target_slot" BG_PRIORITY="$promote_priority" BG_API_PRIORITY=$((promote_priority + 1)) compose "$target_project" up -d --no-build backend --wait --wait-timeout "$wait_seconds"; wait_for_target_slot "$probe_url"
echo "Promoting $target_slot frontend after API routing is verified..."; BG_SLOT="$target_slot" BG_PRIORITY="$promote_priority" BG_API_PRIORITY=$((promote_priority + 1)) compose "$target_project" up -d --no-build frontend --wait --wait-timeout "$wait_seconds"; wait_for_target_slot "$frontend_url"
if [[ "${BG_KEEP_OLD:-0}" != 1 && "$active_project" != "$target_project" ]]; then echo "Draining old project $active_project for ${drain_seconds}s..."; sleep "$drain_seconds"; compose "$active_project" stop backend frontend quota-refresher notification-dispatcher archive-retention backend-init-db >/dev/null 2>&1 || true; fi
write_state "$target_slot" "$target_project" "$promote_priority"; deployment_succeeded=1; echo "Blue-green deployment complete; active slot is $target_slot."
