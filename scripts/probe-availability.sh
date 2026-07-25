#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/probe-availability.sh [URL] [LOG_FILE]

Probes one public URL once per interval and records every result as TSV.
HTTP responses outside the 2xx range and curl errors, including timeouts, fail.

Environment:
  PROBE_INTERVAL_SECONDS         Delay between probes (default: 1)
  PROBE_CONNECT_TIMEOUT_SECONDS  Connection timeout (default: 0.4)
  PROBE_MAX_TIME_SECONDS         Whole-request timeout (default: 0.8)
  PROBE_DURATION_SECONDS         Stop after this many seconds; 0 runs forever
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

probe_url="${1:-http://localhost:8080/api/health}"
probe_log="${2:-${TMPDIR:-/tmp}/codex-proxy-availability.tsv}"
interval="${PROBE_INTERVAL_SECONDS:-1}"
connect_timeout="${PROBE_CONNECT_TIMEOUT_SECONDS:-0.4}"
max_time="${PROBE_MAX_TIME_SECONDS:-0.8}"
duration="${PROBE_DURATION_SECONDS:-0}"
error_file=$(mktemp "${TMPDIR:-/tmp}/codex-proxy-probe-error.XXXXXX")
started_at=$SECONDS
requests=0
failures=0

finish() {
  local exit_code=$?
  trap - EXIT INT TERM
  printf '# summary\trequests=%d\tfailures=%d\n' "$requests" "$failures" >> "$probe_log"
  rm -f -- "$error_file"
  exit "$exit_code"
}
trap finish EXIT INT TERM

mkdir -p -- "$(dirname -- "$probe_log")"
printf 'timestamp\tresult\tcurl_exit\thttp_status\ttime_total\tremote_ip\terror\n' > "$probe_log"

while (( duration == 0 || SECONDS - started_at < duration )); do
  : > "$error_file"
  set +e
  metrics=$(curl --silent --show-error --insecure \
    --connect-timeout "$connect_timeout" \
    --max-time "$max_time" \
    --output /dev/null \
    --write-out $'%{http_code}\t%{time_total}\t%{remote_ip}' \
    "$probe_url" 2>"$error_file")
  curl_exit=$?
  set -e

  IFS=$'\t' read -r http_status time_total remote_ip <<< "$metrics"
  error=$(tr '\t\r\n' '   ' < "$error_file")
  result=fail
  if (( curl_exit == 0 )) && [[ "$http_status" =~ ^2[0-9][0-9]$ ]]; then
    result=ok
  else
    failures=$((failures + 1))
  fi
  requests=$((requests + 1))
  printf '%s\t%s\t%d\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" "$result" "$curl_exit" \
    "${http_status:-000}" "${time_total:-0}" "${remote_ip:--}" "$error" >> "$probe_log"
  sleep "$interval"
done
