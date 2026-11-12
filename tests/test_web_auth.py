"""网页 API 的 401 不应带 WWW-Authenticate，避免登录后再弹出浏览器验证框。"""


def test_api_unauthorized_has_no_www_authenticate(client):
    resp = client.get("/api/books")
    assert resp.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in resp.headers}


def test_api_wrong_password_has_no_www_authenticate(client):
    resp = client.get("/api/books", auth=("admin", "wrong"))
    assert resp.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in resp.headers}


def test_opds_unauthorized_still_sends_www_authenticate(client):
    resp = client.get("/opds")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").startswith("Basic")
