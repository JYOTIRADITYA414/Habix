import re

import pytest

from habix import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-secret",
        "DATABASE": str(tmp_path / "habix-test.sqlite3"),
        "SESSION_COOKIE_SECURE": False,
    })
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/login"):
    response = client.get(path)
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, f"No CSRF token on {path}"
    return match.group(1).decode()


def register(client, name="Alex Student", email="alex@example.test", password="correct-horse-123"):
    token = csrf(client, "/register")
    return client.post("/register", data={"csrf_token": token, "name": name, "email": email, "password": password}, follow_redirects=True)


@pytest.fixture
def register_user():
    return register


@pytest.fixture
def csrf_token():
    return csrf
