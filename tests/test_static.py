"""Static file mount tests for M3.T1.

Verifies that the FastAPI app serves `app/static/style.css` at
`/static/style.css` with `Content-Type: text/css`, per the ticket
acceptance criteria.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_static_style_css_returns_200() -> None:
    response = client.get("/static/style.css")
    assert response.status_code == 200


def test_static_style_css_content_type_is_text_css() -> None:
    response = client.get("/static/style.css")
    content_type = response.headers["content-type"].lower()
    assert content_type.startswith("text/css"), (
        f"Expected text/css, got {content_type!r}"
    )


def test_static_style_css_body_is_non_empty() -> None:
    response = client.get("/static/style.css")
    assert len(response.text) > 0


def test_static_style_css_uses_dark_palette() -> None:
    """NeetCode-inspired palette per the ticket brief: bg #0d1117, cards #161b22, solved #3fb950."""
    response = client.get("/static/style.css")
    css = response.text
    assert "#0d1117" in css, "background color #0d1117 missing"
    assert "#161b22" in css, "card color #161b22 missing"
    assert "#3fb950" in css, "solved color #3fb950 missing"


def test_base_html_references_static_stylesheet() -> None:
    """base.html must link the served stylesheet."""
    response = client.get("/")
    assert 'href="/static/style.css"' in response.text
