def test_public_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert response.headers["x-deployment-test"] == "short-timeout-probe-v1"
