# Path: tests/test_installer.py
# Description: Regression coverage for native Codex CLI proxy configuration.

import os
import subprocess
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[2] / "frontend" / "public" / "install.sh"
DASHBOARD_USERS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "(dashboard)" / "users" / "page.tsx"


def test_unix_installer_updates_native_config_and_removes_legacy_wrappers():
    script = INSTALLER.read_text()

    assert 'CONFIG_FILE="$CODEX_DIR/config.toml"' in script
    assert 'print "model_provider = \\"codex_proxy\\""' in script
    assert "[model_providers.codex_proxy]" in script
    assert "[model_providers.codex_proxy.auth]" in script
    assert 'cat > "$TOKEN_HELPER"' in script
    assert 'remove_legacy_wrapper "$USER_BIN_DIR/codex"' in script
    assert 'remove_legacy_wrapper "$USER_BIN_DIR/codex-proxy"' in script
    assert 'remove_legacy_wrapper "$USER_BIN_DIR/codex-direct"' in script


def test_dashboard_setup_commands_never_include_the_revealed_key():
    source = DASHBOARD_USERS.read_text()

    unix_command = next(line for line in source.splitlines() if "const unixCmd" in line)
    windows_command = next(line for line in source.splitlines() if "const winCmd" in line)

    assert "secret" not in unix_command
    assert "secret" not in windows_command
    assert "CODEX_PROXY_KEY" not in windows_command


def test_unix_installer_reads_key_from_file_without_putting_it_in_argv(tmp_path):
    codex_home = tmp_path / "codex-home"
    key_file = tmp_path / "user-key"
    key_file.write_text("usr_test_secret_without_a_trailing_newline")
    key_file.chmod(0o600)

    env = os.environ.copy()
    env.update({"CODEX_HOME": str(codex_home), "CODEX_PROXY_KEY_FILE": str(key_file)})
    result = subprocess.run(
        ["bash", str(INSTALLER), "https://proxy.example.test/api"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (codex_home / "codex-proxy.key").read_text() == "usr_test_secret_without_a_trailing_newline\n"
    config = (codex_home / "config.toml").read_text()
    assert 'base_url = "https://proxy.example.test/api/v1"' in config
    assert "usr_test_secret" not in config


def test_unix_installer_rejects_legacy_key_argument(tmp_path):
    env = os.environ.copy()
    env.update({"CODEX_HOME": str(tmp_path / "codex-home")})
    result = subprocess.run(
        ["bash", str(INSTALLER), "https://proxy.example.test/api", "usr_leaked_in_argv"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert "usr_leaked_in_argv" not in result.stderr
    assert not (tmp_path / "codex-home" / "codex-proxy.key").exists()
