#!/usr/bin/env bash
# Lightweight Codex Proxy pool status line.  Codex Code can invoke this with
# a JSON session on stdin; quota data always comes from the pooled /me endpoint.
set -uo pipefail

cat >/dev/null 2>&1 || true
base="${CODEX_BASE_URL:-${OPENAI_BASE_URL:-}}"
token="${CODEX_AUTH_TOKEN:-${OPENAI_API_KEY:-}}"
if [ -z "$base" ] || [ -z "$token" ]; then
  printf 'codex-proxy: status unavailable\n'
  exit 0
fi

cache="${TMPDIR:-/tmp}/codex-proxy-usage-$(id -u).json"
tmp="${cache}.tmp.$$"
usage_url="${base%/}"
case "$usage_url" in
  */api/v1) usage_url+="/me/usage" ;;
  */api) usage_url+="/v1/me/usage" ;;
  *) usage_url+="/api/v1/me/usage" ;;
esac
if curl -fsS --max-time 5 -H "Authorization: Bearer ${token}" \
  "$usage_url" -o "$tmp" 2>/dev/null; then
  mv -f "$tmp" "$cache"
else
  rm -f "$tmp"
fi

if [ ! -s "$cache" ]; then
  printf 'codex-proxy: usage unavailable\n'
  exit 0
fi

five="$(jq -r '.pool.five_hour.used_pct // .pool.monthly.used_pct // empty' "$cache" 2>/dev/null)"
week="$(jq -r '.pool.weekly.used_pct // empty' "$cache" 2>/dev/null)"
fmt() { awk -v n="${1:-0}" 'BEGIN { printf "%.0f%%", n*100 }'; }
line="Codex pool"
[ -n "$five" ] && line+=" · 5h $(fmt "$five")"
[ -n "$week" ] && line+=" · 7d $(fmt "$week")"
printf '%s\n' "$line"
