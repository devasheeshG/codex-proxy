import httpx

from app.utils.models.api import ProviderHealth
from app.utils.provider_health import classify_failure


def test_authenticated_endpoint_401_requires_reauthentication():
    request = httpx.Request("GET", "https://chatgpt.com/backend-api/wham/usage")
    response = httpx.Response(401, request=request)
    health, code, _ = classify_failure(httpx.HTTPStatusError("revoked", request=request, response=response))

    assert health is ProviderHealth.REAUTH_REQUIRED
    assert code == "oauth_refresh_rejected"


def test_authenticated_endpoint_403_remains_degraded():
    request = httpx.Request("GET", "https://chatgpt.com/backend-api/wham/usage")
    response = httpx.Response(403, request=request)
    health, code, _ = classify_failure(httpx.HTTPStatusError("forbidden", request=request, response=response))

    assert health is ProviderHealth.DEGRADED
    assert code == "provider_access_rejected"
