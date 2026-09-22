import base64

import pytest

from app.scripts import egress_relay
from app.utils import egress


def test_empty_config_preserves_direct_network_path():
    targets = egress.parse_targets("")

    assert [target.id for target in targets] == ["direct"]
    assert targets[0].kind == "direct"


def test_unassigned_requests_use_first_enabled_target_without_rotation():
    targets = egress.parse_targets('[{"id":"first","kind":"direct"},{"id":"second","kind":"direct"}]')
    pool = egress.EgressPool(targets)

    assert pool.default_target().id == "first"
    assert [pool.resolve(None).id for _ in range(4)] == ["first"] * 4


def test_proxy_target_parsing_keeps_credentials_private():
    targets = egress.parse_targets(
        '[{"id":"eni-1:172.31.1.2","label":"ens5 · 172.31.1.2",'
        '"kind":"proxy","proxy_url":"http://host.docker.internal:18081","relay_auth":true,'
        '"private_ip":"172.31.1.2","public_ip":"203.0.113.10"}]'
    )

    assert targets[0].proxy_url.endswith(":18081")
    assert "proxy_url" not in targets[0].public_dict()
    assert targets[0].public_dict()["public_ip"] == "203.0.113.10"


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        '[{"id":"bad id","kind":"direct"}]',
        '[{"id":"duplicate","kind":"direct"},{"id":"duplicate","kind":"direct"}]',
        '[{"id":"proxy","kind":"proxy"}]',
    ],
)
def test_invalid_target_configs_fail_closed(raw):
    with pytest.raises(egress.EgressConfigurationError):
        egress.parse_targets(raw)


def test_relay_parses_authenticated_connect_request():
    authorization = base64.b64encode(b"proxy:test-token").decode()
    host, port, headers = egress_relay._parse_connect_request(
        (f"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\nProxy-Authorization: Basic {authorization}\r\n\r\n").encode()
    )

    assert host == "chatgpt.com"
    assert port == 443
    assert headers["proxy-authorization"] == f"Basic {authorization}"
    assert egress_relay._target_host_allowed(host, ("chatgpt.com", ".openai.com"))
    assert not egress_relay._target_host_allowed("attacker-openai.com", (".openai.com",))


def test_relay_target_config_uses_proxy_port_and_private_ip():
    targets = egress_relay._parse_targets(
        '[{"id":"egress-a","kind":"proxy","proxy_url":"http://host:18082","private_ip":"172.31.1.5","enabled":true}]'
    )

    assert targets == (egress_relay.RelayTarget("egress-a", 18082, "172.31.1.5"),)


def test_dashboard_lists_egress_targets(client, admin_headers):
    response = client.get("/api/v1/accounts/egress-targets", headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {
        "targets": [
            {
                "id": "direct",
                "label": "Default server network path",
                "kind": "direct",
                "interface_name": None,
                "private_ip": None,
                "public_ip": None,
                "max_concurrency": 32,
                "enabled": True,
            }
        ]
    }


def test_account_egress_target_can_be_pinned_and_defaults_when_cleared(client, admin_headers, seed_account):
    account_id = seed_account("egress-binding")

    pinned = client.put(
        f"/api/v1/accounts/{account_id}",
        headers=admin_headers,
        json={"egress_target_id": "direct"},
    )
    assert pinned.status_code == 200
    assert pinned.json()["account"]["egress_target_id"] == "direct"

    cleared = client.put(
        f"/api/v1/accounts/{account_id}",
        headers=admin_headers,
        json={"egress_target_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["account"]["egress_target_id"] == "direct"


def test_account_rejects_unknown_egress_target(client, admin_headers, seed_account):
    account_id = seed_account("unknown-egress")

    response = client.put(
        f"/api/v1/accounts/{account_id}",
        headers=admin_headers,
        json={"egress_target_id": "missing"},
    )

    assert response.status_code == 422
    assert "not configured" in response.json()["detail"]
