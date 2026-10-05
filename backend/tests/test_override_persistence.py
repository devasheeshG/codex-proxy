"""Persist user override intent independently from the preset's current value."""

import pytest
from sqlalchemy import text

from app.scripts.migrate import sync_canonical_schema
from app.utils import presets
from app.utils.postgres.base import engine


@pytest.mark.parametrize("field", list(presets.POLICY_COLUMNS))
def test_explicit_same_value_override_survives_reload_and_preset_edit(client, admin_headers, field):
    preset = client.post("/api/v1/presets", headers=admin_headers, json={"name": "Baseline"}).json()
    user = client.post("/api/v1/users", headers=admin_headers, json={"name": "override-reload", "preset_id": preset["id"]}).json()["user"]
    path = f"/api/v1/users/{user['id']}"
    updated = client.put(path, headers=admin_headers, json={field: user[field]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["user"]["preset_overrides"] == [field]

    def reloaded():
        return next(u for u in client.get("/api/v1/users", headers=admin_headers).json()["users"] if u["id"] == user["id"])

    assert reloaded()["preset_overrides"] == [field]
    payload = {key: preset[key] for key in presets.POLICY_COLUMNS}
    payload["name"] = "Renamed baseline"
    response = client.put(f"/api/v1/presets/{preset['id']}", headers=admin_headers, json=payload)
    assert response.status_code == 200, response.text
    assert reloaded()["preset_overrides"] == [field]
    assert reloaded()[field] == user[field]
    cleared = client.delete(f"{path}/preset-overrides/{field}", headers=admin_headers)
    assert cleared.status_code == 200, cleared.text
    assert reloaded()["preset_overrides"] == []


def test_explicit_false_context_override_stays_false_after_preset_changes(client, admin_headers):
    preset = client.post("/api/v1/presets", headers=admin_headers, json={"name": "Context"}).json()
    user = client.post(
        "/api/v1/users", headers=admin_headers, json={"name": "context-off", "preset_id": preset["id"], "allow_extended_context": False}
    ).json()["user"]
    assert user["preset_overrides"] == ["allow_extended_context"]
    response = client.put(f"/api/v1/presets/{preset['id']}", headers=admin_headers, json={"name": "Context", "allow_extended_context": True})
    assert response.status_code == 200, response.text
    stored = next(u for u in client.get("/api/v1/users", headers=admin_headers).json()["users"] if u["id"] == user["id"])
    assert stored["allow_extended_context"] is False
    assert stored["preset_overrides"] == ["allow_extended_context"]


def test_devasheesh_marker_is_repaired_and_protection_survives_clear(client, admin_headers):
    preset = client.post("/api/v1/presets", headers=admin_headers, json={"name": "Protected context"}).json()
    user = client.post("/api/v1/users", headers=admin_headers, json={"name": "Devasheesh", "preset_id": preset["id"]}).json()["user"]
    assert user["allow_extended_context"] is True
    assert user["preset_overrides"] == ["allow_extended_context"]
    # Reproduce the persisted marker loss from the previous release.
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET preset_overrides_json = '[]' WHERE id = :id"), {"id": user["id"]})
    sync_canonical_schema()
    sync_canonical_schema()
    stored = next(u for u in client.get("/api/v1/users", headers=admin_headers).json()["users"] if u["id"] == user["id"])
    assert stored["allow_extended_context"] is True
    assert stored["preset_overrides"] == ["allow_extended_context"]
    cleared = client.delete(f"/api/v1/users/{user['id']}/preset-overrides/allow_extended_context", headers=admin_headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["user"]["allow_extended_context"] is True
    assert cleared.json()["user"]["preset_overrides"] == ["allow_extended_context"]
