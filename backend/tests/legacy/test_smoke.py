def test_health(client):
    assert client.get("/api/health").get_json() == {"status": "ok"}


def test_ready(client):
    r = client.get("/api/ready")
    assert r.status_code == 200, r.get_json()


def test_login_and_me(client, users, headers):
    r = client.get("/api/auth/me", headers=headers["hr"])
    assert r.status_code == 200 and r.get_json()["roles"] == ["hr"]
