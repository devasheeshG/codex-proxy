#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: curl -fsSL <PROXY_ORIGIN>/install.sh | bash -s -- <API_BASE_URL>" >&2
  echo "The installer prompts for the API key without echoing it." >&2
  echo "For automation, set CODEX_PROXY_KEY_FILE to a protected file containing the key." >&2
  exit 1
}

if [ "$#" -ne 1 ]; then
  usage
fi

PROXY_BASE_URL="$1"
PROXY_USER_KEY=""

case "$PROXY_BASE_URL" in
  http://*|https://*) ;;
  *) echo "API base URL must start with http:// or https://"; exit 1 ;;
esac
case "$PROXY_BASE_URL" in
  *\"*|*\\*|*$'\n'*|*$'\r'*) echo "API base URL contains unsupported characters"; exit 1 ;;
esac

read_proxy_key() {
  if [ -n "${CODEX_PROXY_KEY_FILE:-}" ]; then
    if [ ! -f "$CODEX_PROXY_KEY_FILE" ] || [ ! -r "$CODEX_PROXY_KEY_FILE" ]; then
      echo "CODEX_PROXY_KEY_FILE must name a readable regular file." >&2
      exit 1
    fi

    # `read` returns non-zero when the final line has no newline, even though it
    # populated the variable. Accept that common secret-file format.
    IFS= read -r PROXY_USER_KEY < "$CODEX_PROXY_KEY_FILE" || [ -n "$PROXY_USER_KEY" ]
  elif [ -t 2 ] && [ -r /dev/tty ]; then
    printf 'Paste the Codex Proxy API key (input hidden): ' > /dev/tty
    if ! IFS= read -r -s PROXY_USER_KEY < /dev/tty; then
      printf '\nUnable to read the API key from the terminal.\n' > /dev/tty
      exit 1
    fi
    printf '\n' > /dev/tty
  else
    echo "No interactive terminal is available." >&2
    echo "Set CODEX_PROXY_KEY_FILE to a protected file containing the API key." >&2
    exit 1
  fi

  if [ -z "$PROXY_USER_KEY" ]; then
    echo "API key cannot be empty." >&2
    exit 1
  fi
}

read_proxy_key

CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
CONFIG_FILE="$CODEX_DIR/config.toml"
BIN_DIR="$CODEX_DIR/bin"
KEY_FILE="$CODEX_DIR/codex-proxy.key"
TOKEN_HELPER="$BIN_DIR/codex-proxy-token"
MODEL_BASE_URL="${PROXY_BASE_URL%/}/v1"

mkdir -p "$BIN_DIR"
umask 077
printf '%s\n' "$PROXY_USER_KEY" > "$KEY_FILE"
unset PROXY_USER_KEY

cat > "$TOKEN_HELPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
IFS= read -r token < "$KEY_FILE"
printf '%s' "\$token"
EOF
chmod 700 "$TOKEN_HELPER"

# Preserve every unrelated Codex setting while replacing the active provider and
# this installer's provider tables. The backup is the config from immediately
# before the most recent installer run.
if [ -f "$CONFIG_FILE" ]; then
  cp -p "$CONFIG_FILE" "$CONFIG_FILE.bak"
fi

TEMP_CONFIG="$(mktemp "$CODEX_DIR/config.toml.tmp.XXXXXX")"
trap 'rm -f "$TEMP_CONFIG"' EXIT
CONFIG_SOURCE="$CONFIG_FILE"
if [ ! -f "$CONFIG_SOURCE" ]; then
  CONFIG_SOURCE=/dev/null
fi

awk '
  BEGIN { seen_table = 0; inserted_provider = 0; skip_proxy = 0 }

  function insert_provider() {
    if (!inserted_provider) {
      print "model_provider = \"codex_proxy\""
      print ""
      inserted_provider = 1
    }
  }

  /^[[:space:]]*\[/ {
    if ($0 ~ /^[[:space:]]*\[model_providers\.codex_proxy(\.|\])/) {
      skip_proxy = 1
      seen_table = 1
      next
    }
    skip_proxy = 0
    insert_provider()
    seen_table = 1
  }

  skip_proxy { next }
  !seen_table && /^[[:space:]]*model_provider[[:space:]]*=/ { next }
  { print }

  END { insert_provider() }
' "$CONFIG_SOURCE" > "$TEMP_CONFIG"

cat >> "$TEMP_CONFIG" <<EOF

[model_providers.codex_proxy]
name = "OpenAI"
base_url = "$MODEL_BASE_URL"
wire_api = "responses"
supports_websockets = false
# Allow long-running reasoning turns to remain connected while the model is
# thinking before the next SSE frame arrives.
stream_idle_timeout_ms = 900000

[model_providers.codex_proxy.auth]
command = "$TOKEN_HELPER"
refresh_interval_ms = 0
EOF

mv "$TEMP_CONFIG" "$CONFIG_FILE"
trap - EXIT
chmod 600 "$CONFIG_FILE" "$KEY_FILE"

# Clean up launchers made by older versions of this installer. Never remove a
# user-owned command that does not contain the old installer's signature.
remove_legacy_wrapper() {
  local wrapper="$1"
  if [ -f "$wrapper" ] && grep -Eq 'codex-proxy-real-cli|--profile proxy' "$wrapper"; then
    rm -f "$wrapper"
  fi
}

USER_BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
remove_legacy_wrapper "$USER_BIN_DIR/codex"
remove_legacy_wrapper "$USER_BIN_DIR/codex-proxy"
remove_legacy_wrapper "$USER_BIN_DIR/codex-direct"
rm -f "$CODEX_DIR/proxy.config.toml" "$CODEX_DIR/codex-proxy-real-cli"

echo
echo "Codex CLI configured to use Codex Proxy."
echo "  Config: $CONFIG_FILE"
echo "  API:    $MODEL_BASE_URL"
echo
echo "Run: codex"
