"""Template rendering tests for M3.T1 — base.html + sidebar.

Verifies that the FastAPI app mounts a minimal test route that
extends `app/templates/base.html` and that the rendered HTML
contains every sidebar link required by the ticket acceptance
criteria (Roadmap, Problems, Profile, Logout).
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


EXPECTED_SIDEBAR_LINKS = ["Roadmap", "Problems", "Profile", "Logout"]


def test_root_route_returns_200() -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_root_route_renders_base_html() -> None:
    response = client.get("/")
    # base.html sets a recognizable document title via the {% block title %}
    # override on the test route — see app/main.py.
    assert "<!DOCTYPE html>" in response.text or "<!doctype html>" in response.text.lower()
    assert "<html" in response.text.lower()


def test_sidebar_contains_all_required_links() -> None:
    response = client.get("/")
    body = response.text
    for link_label in EXPECTED_SIDEBAR_LINKS:
        assert link_label in body, f"Sidebar missing link: {link_label!r}"


def test_sidebar_links_point_to_expected_routes() -> None:
    response = client.get("/")
    body = response.text
    # hrefs must match the agreed routes per the ticket brief.
    assert 'href="/roadmap"' in body
    assert 'href="/problems"' in body
    assert 'href="/profile"' in body
    # Logout is a POST-only route per the brief; the sidebar uses a
    # tiny form so plain GET navigation never logs the user out.
    assert 'action="/logout"' in body
    assert 'method="post"' in body.lower()


def test_template_includes_content_block() -> None:
    """base.html must declare a `content` block for child templates to fill."""
    response = client.get("/")
    # The test page provides its own body content; assert that the
    # sidebar+content layout actually rendered the child content.
    assert "M3.T1 smoke page" in response.text
