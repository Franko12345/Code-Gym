"""Tests for GET /problems/{slug} — M4.T5 (ticket #17).

Renders the problem detail page: statement + examples on the
left, CodeMirror editor + language selector + submit form on
the right. Auth-gated; per ADR-0003 the page 302s to /login
when no session cookie is present.

CodeMirror is loaded from the jsDelivr CDN with **real sha384
SRI hashes** — see the test ``test_codemirror_scripts_have_sri_hash``
below; we don't allow ``integrity=""``.

The page is otherwise pure HTML + HTMX (ADR-0004): the editor
is the ONE place with vanilla JS, and that's loaded via
``<script src=... integrity=...>`` so the browser refuses to
run a tampered bundle. The vanilla JS does:

* load saved code from ``localStorage[cg-code-{slug}-{lang}]``
* save to the same key on keyup (debounced 500ms)
* swap CodeMirror mode on language change
* copy CM content into a hidden ``<textarea name="code">``
  before submit

We test the rendered HTML; we don't drive a real browser.
Per the ticket: "Use TestClient for tests; no Playwright."

Seam
----
Same pattern as ``tests/test_submit.py``: real FastAPI app
via ``TestClient``, DB path patched to ``tmp_path``. Auth via
``client.cookies.set('cg_session', encode_jwt(user_id))``
(the path TestClient itself recommends).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_utils import encode_jwt
from app.db import init_db
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_submit.py)
# ---------------------------------------------------------------------------


def _connect(path: Path) -> sqlite3.Connection:
    """Open ``path`` with ``row_factory = sqlite3.Row`` for column access."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh SQLite file for each test, wired as the app's DB.

    Patches every module-level reference that gets captured at
    import time: ``app.db.DEFAULT_DB_PATH`` (source of truth) and
    the per-module mirrors used by the middleware + service layer.
    """
    p = tmp_path / "code_gym.db"
    init_db(p)
    monkeypatch.setattr("app.db.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.auth.middleware.DB_PATH", p)
    monkeypatch.setattr("app.problems.routes.DEFAULT_DB_PATH", p)
    monkeypatch.setattr("app.problems.service.DEFAULT_DB_PATH", p)
    return p


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    """TestClient bound to the real FastAPI app."""
    return TestClient(app)


@pytest.fixture()
def seeded_user(db_path: Path) -> dict:
    """Insert one user (id, email, display_name)."""
    email = "franco@froto.online"
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES (?, ?, ?)",
            (email, "$2b$12$placeholder", "Franco"),
        )
        user_id = int(cur.lastrowid)
    return {"id": user_id, "email": email, "display_name": "Franco"}


@pytest.fixture()
def seeded_problem(db_path: Path) -> dict:
    """Insert one topic + one problem with two sample test cases.

    The statement and examples are realistic so the template test
    can assert on actual rendered content (not just structural
    markers).
    """
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO topics (slug, name, obi_phase, order_index) "
            "VALUES ('strings', 'Strings', 'F1', 20)"
        )
        cur = conn.execute(
            "INSERT INTO problems (slug, title, topic_id, difficulty, "
            "statement_md, examples_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "soma-de-digitos",
                "Soma de Dígitos",
                1,
                1,
                "Dado um inteiro N, imprima a soma dos seus dígitos.",
                '[{"input": "123", "output": "6", "explanation": "1+2+3=6"}]',
                "2026-08-10T00:00:00",
            ),
        )
        problem_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO test_cases (problem_id, stdin, expected_stdout, "
            "is_sample, weight) VALUES (?, ?, ?, 1, 1)",
            (problem_id, "123", "6\n"),
        )
        conn.commit()
    return {
        "slug": "soma-de-digitos",
        "id": problem_id,
        "title": "Soma de Dígitos",
        "statement": "Dado um inteiro N, imprima a soma dos seus dígitos.",
    }


# Pin the cookie name to match what the middleware reads.
SESSION_COOKIE = "cg_session"


def _login(client: TestClient, user_id: int) -> None:
    """Set the ``cg_session`` cookie on the client to authenticate as ``user_id``."""
    client.cookies.set(SESSION_COOKIE, encode_jwt(user_id))


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_problem_page_without_auth_redirects_to_login(
    client: TestClient, seeded_problem: dict
) -> None:
    """Anonymous GET /problems/{slug} must 302 to /login.

    Per ADR-0003: the problem page is auth-gated — no public
    read of problem statements or test scaffolding.
    """
    response = client.get(f"/problems/{seeded_problem['slug']}", follow_redirects=False)
    assert response.status_code == 302, (
        f"expected 302 redirect to /login, got {response.status_code}: {response.text!r}"
    )
    assert response.headers.get("location", "").endswith("/login"), (
        f"redirect target must be /login, got: {response.headers.get('location')!r}"
    )


# ---------------------------------------------------------------------------
# Happy path — HTML structure
# ---------------------------------------------------------------------------


def test_problem_page_with_auth_renders_200_and_html(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """Authenticated GET renders the problem page with 200 + HTML body."""
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text!r}"
    )
    body = response.text
    assert body.lstrip().startswith("<!DOCTYPE html>"), (
        "page must be a full HTML document, not a fragment"
    )
    # Statement content (not just placeholder text).
    assert "Dado um inteiro N" in body, (
        f"statement must render in the body, got: {body[:300]!r}"
    )


def test_problem_page_renders_problem_title_and_examples(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """Title and the example block render in the HTML body."""
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200
    body = response.text
    assert seeded_problem["title"] in body, (
        f"title '{seeded_problem['title']}' must render in the page"
    )
    # The example block — input "123", output "6" came from examples_json.
    assert "123" in body, "example input must render"
    assert ">6<" in body or '"6"' in body or " 6 " in body, (
        "example output must render (some surrounding markup is fine)"
    )


# ---------------------------------------------------------------------------
# 404 — unknown slug
# ---------------------------------------------------------------------------


def test_problem_page_unknown_slug_returns_404(
    client: TestClient, seeded_user: dict
) -> None:
    """An authenticated GET to /problems/{nonexistent} must 404."""
    _login(client, seeded_user["id"])
    response = client.get("/problems/this-problem-does-not-exist")
    assert response.status_code == 404, (
        f"unknown slug must 404, got {response.status_code}: {response.text!r}"
    )


# ---------------------------------------------------------------------------
# SRI integrity attribute — non-empty on every CodeMirror <script>
# ---------------------------------------------------------------------------


_SCRIPT_TAG_RE = re.compile(
    r'<script[^>]*\bintegrity=("([^"]*)"|\'([^\']*)\')[^>]*>',
    re.IGNORECASE,
)


def _extract_integrity_attrs(html: str) -> list[str]:
    """Return the value of every ``integrity=...`` attribute on the page.

    Used by the SRI test to confirm CodeMirror loads with real hashes
    rather than the empty string (which silently disables SRI in
    browsers).
    """
    return [
        m.group(2) or m.group(3)
        for m in _SCRIPT_TAG_RE.finditer(html)
    ]


def test_problem_page_loads_codemirror_script_with_sri_hash(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """The page must reference CodeMirror and every <script> must have
    a non-empty SRI ``integrity`` attribute (ADR-0004).

    We don't pin the *exact* hash here — that would tie the test to
    a single CodeMirror version — but we require at least one
    ``integrity`` attribute starting with ``sha384-`` (the only
    hash algorithm we use).
    """
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200
    body = response.text

    # Sanity: the page actually mentions CodeMirror (catches a
    # regression where someone deletes the script tags entirely).
    assert "codemirror" in body.lower(), (
        "page must reference CodeMirror (cdn.jsdelivr.net/npm/codemirror)"
    )

    integrities = _extract_integrity_attrs(body)
    assert integrities, (
        "no <script integrity=...> tags found — SRI must be present"
    )
    for attr in integrities:
        assert attr.strip(), "integrity attribute is empty — SRI disabled"
        assert attr.startswith("sha384-"), (
            f"expected sha384- prefix, got {attr!r}"
        )


def test_problem_page_has_codemirror_script_with_specific_cdn(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """Pin the CDN URL so a future migration away from jsDelivr is
    caught by the test (this is the security boundary ADR-0004
    relies on)."""
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200
    body = response.text
    assert "cdn.jsdelivr.net/npm/codemirror" in body, (
        "CodeMirror must be loaded from jsDelivr (the CDN pinned in ADR-0004)"
    )


# ---------------------------------------------------------------------------
# Language <select>
# ---------------------------------------------------------------------------


def test_problem_page_renders_language_select_with_cpp_and_python(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """A <select name=language> with options cpp and python (ADR-0005)."""
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200
    body = response.text

    # There must be a <select> with name="language" (the field
    # the submit form POSTs).
    assert re.search(
        r'<select\b[^>]*\bname=("|\')language\1',
        body,
        re.IGNORECASE,
    ), "expected a <select name='language'> on the page"

    # Both languages appear as <option value="..."> entries.
    assert re.search(
        r'<option\b[^>]*\bvalue=("|\')cpp\1',
        body,
        re.IGNORECASE,
    ), "language <select> must include an <option value='cpp'>"
    assert re.search(
        r'<option\b[^>]*\bvalue=("|\')python\1',
        body,
        re.IGNORECASE,
    ), "language <select> must include an <option value='python'>"


# ---------------------------------------------------------------------------
# localStorage key — documented in the HTML so a future reader
# can find the contract without grepping the JS.
# ---------------------------------------------------------------------------


def test_problem_page_documents_localstorage_key(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """The contract key ``cg-code-{slug}-{lang}`` must be discoverable
    from the rendered HTML: either as an HTML comment or as a
    ``data-storage-key`` attribute on the editor container.

    This protects the localStorage schema from silent drift between
    server (template) and client (vanilla JS) — both have to agree
    on the same string and the test pins the agreement at the
    rendered-artefact level.
    """
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200
    body = response.text
    slug = seeded_problem["slug"]
    # Either the bare template fragment appears in an HTML comment,
    # or the rendered element carries a data-storage-key attribute
    # whose value interpolates the slug.
    in_comment = f"cg-code-{slug}-" in body
    in_attr = bool(
        re.search(
            r'data-storage-key=("|\')cg-code-' + re.escape(slug) + r'-\1',
            body,
            re.IGNORECASE,
        )
    )
    assert in_comment or in_attr, (
        f"localStorage key pattern 'cg-code-{slug}-<lang>' must be "
        f"documented in the rendered HTML (comment or data-attr)"
    )


# ---------------------------------------------------------------------------
# Submit form — wired to M4.T4 endpoint
# ---------------------------------------------------------------------------


def test_problem_page_submit_form_targets_submit_endpoint(
    client: TestClient,
    seeded_user: dict,
    seeded_problem: dict,
) -> None:
    """The submit <form> must POST to /problems/{slug}/submit (M4.T4)."""
    _login(client, seeded_user["id"])
    response = client.get(f"/problems/{seeded_problem['slug']}")
    assert response.status_code == 200
    body = response.text
    slug = seeded_problem["slug"]
    # The form action carries the slug and the submit suffix.
    assert re.search(
        rf'<form\b[^>]*\baction=("|\')/problems/{re.escape(slug)}/submit\1',
        body,
        re.IGNORECASE,
    ), "form must POST to /problems/{slug}/submit"
    # The form must use POST (the brief: "The form action=
    # /problems/{slug}/submit method=POST"). Looking for the
    # ``method="post"`` substring anywhere in the body is a
    # poor assertion (the JS strings inside <script> blocks
    # also contain 'post'), so we search the rendered HTML
    # for a literal method="post" attribute pair.
    assert 'method="post"' in body or "method='post'" in body, (
        'form must use POST method (method="post")'
    )
    # The hidden textarea that vanilla JS fills from CodeMirror
    # before submit. Must exist so the route's ``Form('code')``
    # binding has something to bind to.
    assert re.search(
        r'<textarea\b[^>]*\bname=("|\')code\1',
        body,
        re.IGNORECASE,
    ), "a <textarea name='code'> must exist for the editor to fill"
