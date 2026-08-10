"""Scrape past OBI (Olimpíada Brasileira de Informática) problems
and write a snapshot YAML that ``scripts.seed`` can load.

Per ADR-0006, this script is **manual** — no cron. It runs on
demand when the maintainer wants to refresh content, and the
output YAML is reviewed before commit.

Source: https://olimpiada.ic.unicamp.br/pratique/p1/ — static HTML
pages, no login required, no rate-limit headers observed. We
respect a 1 req/sec cadence with retries-with-backoff anyway as
defense-in-depth (per security-guardian: never blast a remote).

MVP scope (extending is manual): OBI 2019 Fase 1, level ``p1``
(Programação Nível 1). The official site also has phases 2 and 3
and a separate Iniciação track — adding those is a matter of
calling :func:`scrape_obi` again with different ``year``/``phase``
arguments. Keep this script single-purpose and small.

Per ADR-0006 best-effort: the script logs failures and ``exit 0``
even on partial success. The output snapshot may contain fewer
problems than the year/phase actually has — that's the trade-off
the ADR accepted in exchange for never blocking the dev.

Usage::

    python -m scripts.scrape.obi

Output: ``data/snapshots/obi-YYYY-MM-DD.yaml`` — matches the M2.T1
seed schema (``{problems: [{slug, title, topic_slug, ...,
test_cases: [{stdin, expected_stdout, is_sample, weight}]}]}``).

Per security-guardian: no ``shell=True``, no subprocess. Just
``httpx.get(url)`` with a timeout.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "snapshots"
OUTPUT_DIRNAME = "snapshots"

BASE_URL = "https://olimpiada.ic.unicamp.br"
INDEX_URL_TEMPLATE = BASE_URL + "/pratique/p1/"
PROBLEM_URL_TEMPLATE = BASE_URL + "/pratique/p1/{year}/{phase}/{slug}/"

# MVP scope: pick one year/phase combo as documented in ADR-0006.
# To extend, call scrape_obi(year=YEAR, phase=PHASE) again or pass
# different defaults here.
DEFAULT_YEAR = 2019
DEFAULT_PHASE = "f1"  # fase 1

# Defense-in-depth: be polite even though the host doesn't seem to
# rate-limit. 1 req/sec is well below any reasonable threshold.
REQUEST_INTERVAL_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 15.0
RETRY_BACKOFF_SECONDS = (2.0, 4.0, 8.0)  # 3 retries with linear-ish backoff

# Topics we'll attribute scraped problems to. Each OBI problem gets
# topic_slug = "misc" for now — the dev can re-categorise after seed
# by editing the YAML or via a follow-up ticket. A real topic map
# requires reading the problem statement, which is out of scope for
# the auto-scraper.
DEFAULT_TOPIC_SLUG = "misc"

logger = logging.getLogger("scripts.scrape.obi")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class SnapshotSummary:
    """Return value of :func:`scrape_obi`. Lets callers (and tests)
    inspect what happened without re-parsing logs."""

    snapshot_path: str
    problems_fetched: int
    problems_failed: int
    failed_slugs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HTML parsing — stdlib only (no 3rd-party HTML lib per ticket scope)
# ---------------------------------------------------------------------------


class _IndexParser(HTMLParser):
    """Walk the /pratique/p1/ index and collect problem URLs for a
    given year/phase.

    The page is a series of nested ``<ul>`` lists grouped by year
    and phase. Links look like ``/pratique/p1/2019/f1/idade/`` —
    we filter on that exact pattern.
    """

    def __init__(self, year: int, phase: str) -> None:
        super().__init__()
        self._year = year
        self._phase = phase
        self._slugs: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self._href = v
                    break

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._href:
                href = self._href
                self._href = None
                if _is_problem_link(href, self._year, self._phase):
                    slug = _slug_from_href(href)
                    if slug and slug not in self._slugs:
                        self._slugs.append(slug)

    @property
    def slugs(self) -> list[str]:
        return list(self._slugs)


def _is_problem_link(href: str, year: int, phase: str) -> bool:
    """True iff ``href`` points at an OBI problem page for the given
    year/phase combo. E.g. ``/pratique/p1/2019/f1/idade/`` for
    ``year=2019, phase="f1"``."""
    prefix = f"/pratique/p1/{year}/{phase}/"
    return href.startswith(prefix) and href.endswith("/")


def _slug_from_href(href: str) -> str:
    """``/pratique/p1/2019/f1/idade/`` -> ``idade``."""
    parts = href.rstrip("/").split("/")
    return parts[-1] if parts else ""


class _ProblemParser(HTMLParser):
    """Extract title, statement, input/output formats, and example
    (stdin, expected_stdout) pairs from an OBI problem page.

    Page structure we rely on (from the live 2019 F1 pages):

    * ``<h1 class="center">TITLE</h1>`` — the problem title.
    * A block of prose ``<p>...</p>`` between the title and the
      first ``<h3>Entrada</h3>`` — the statement.
    * ``<h3>Entrada</h3>`` then prose until ``<h3>Saída</h3>`` —
      the input format.
    * ``<h3>Saída</h3>`` then prose until ``<h3>Restrições</h3>``
      (or ``Exemplos``) — the output format.
    * ``<h3>Exemplos</h3>`` then one or more ``<table>`` blocks
      with two ``<pre>`` cells: Entrada | Saída.
    """

    def __init__(self) -> None:
        super().__init__()
        self._title: str | None = None
        self._section: str | None = (
            None  # "title" | "statement" | "input" | "output" | "examples" | "after"
        )
        self._current_pre_text: list[str] = []
        self._in_pre: bool = False
        # In the examples section, <b>Entrada</b> and <b>Saída</b>
        # precede each <pre> in alternating <td> cells. Track the
        # bold text we last saw inside the current <td> so we can
        # tag the next <pre> correctly.
        self._td_role: str | None = None  # "in" | "out" — set when we see <b>

        self._statement_buf: list[str] = []
        self._input_buf: list[str] = []
        self._output_buf: list[str] = []
        self._examples: list[tuple[str, str]] = []  # list of (stdin, stdout)
        self._pending_example: dict[str, str | None] = {"in": None, "out": None}

    # -- tag handlers ----------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "h1" and a.get("class") == "center" and self._title is None:
            self._title = ""
            self._section = "title"
            return
        if tag == "h3":
            text_so_far = (a.get("id") or "").lower()
            # We rely on inner text via handle_data; switch section on start.
            self._section = "h3_pending"
            return
        if tag == "pre":
            self._in_pre = True
            self._current_pre_text = []
            return
        if tag == "b" and self._section == "examples":
            self._td_role = "_pending"  # will be set by handle_data
            return
        if tag == "td" and self._section == "examples":
            # Entering a new cell — clear any pending role so a stale
            # role from a previous row doesn't bleed across.
            self._td_role = None
            return
        if tag == "p":
            if self._section in ("statement", "input", "output"):
                self._statement_buf.append("\n")  # placeholder, will keep order
            return

    def _append_prose(self, data: str) -> None:
        """Route a chunk of prose text into the right buffer based on
        the current section."""
        if self._section == "statement":
            self._statement_buf.append(data)
        elif self._section == "input":
            self._input_buf.append(data)
        elif self._section == "output":
            self._output_buf.append(data)

    def handle_data(self, data: str) -> None:
        if self._title is not None and self._section == "title":
            self._title += data
            return
        if self._section == "h3_pending":
            t = data.strip().lower()
            if t.startswith("entrada"):
                self._section = "input"
            elif t.startswith("sa") and "da" in t:  # "saída" / "saida"
                self._section = "output"
            elif t.startswith("exemplos"):
                self._section = "examples"
            elif t.startswith("restri"):
                # End of output section
                self._section = "after"
            else:
                # Heading we don't care about — leave section alone.
                pass
            return
        if self._in_pre:
            self._current_pre_text.append(data)
            return
        if self._td_role == "_pending":
            # We're inside an unclosed <b> in the examples section.
            t = data.strip().lower()
            if t.startswith("entrada"):
                self._td_role = "in"
            elif t.startswith("sa"):
                self._td_role = "out"
            else:
                # Unrecognised bold text — leave as-is so the next
                # <pre> won't grab a bogus role.
                self._td_role = None
            return
        # Plain text in a known prose section
        self._append_prose(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._in_pre:
            self._in_pre = False
            # OBI's <pre> blocks have a leading newline (from the
            # `<pre>\n` on its own line) and often a trailing tab
            # (the indentation of the closing `</pre>`). Strip both.
            text = "".join(self._current_pre_text).strip()
            if self._section == "examples" and self._td_role in ("in", "out"):
                if self._td_role == "in":
                    self._pending_example["in"] = text
                elif self._td_role == "out":
                    self._pending_example["out"] = text
                # If both filled, commit the example.
                if (
                    self._pending_example["in"] is not None
                    and self._pending_example["out"] is not None
                ):
                    self._examples.append(
                        (
                            self._pending_example["in"],
                            self._pending_example["out"],
                        )
                    )
                    self._pending_example = {"in": None, "out": None}
            return
        if tag == "b":
            # Don't clear _td_role here — it must survive </b><pre>
            # so the next <pre> still knows which column it's in.
            return
        if tag == "h1":
            if self._section == "title":
                self._section = "statement"
            return
        # We don't strictly need h3 endtag handling — sections
        # change on the *next* h3's text.

    # -- accessors -------------------------------------------------------

    @property
    def title(self) -> str:
        return (self._title or "").strip()

    @property
    def statement(self) -> str:
        return _collapse_whitespace("".join(self._statement_buf))

    @property
    def input_format(self) -> str:
        return _collapse_whitespace("".join(self._input_buf))

    @property
    def output_format(self) -> str:
        return _collapse_whitespace("".join(self._output_buf))

    @property
    def examples(self) -> list[tuple[str, str]]:
        return list(self._examples)


def _collapse_whitespace(s: str) -> str:
    """Normalise runs of whitespace inside a prose block, preserving
    paragraph breaks (we re-introduce ``\n\n`` between ``<p>`` tags
    in the parser)."""
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def parse_problem_page(
    html: str, *, source: str, source_url: str
) -> dict[str, Any]:
    """Parse one OBI problem page and return the fields ``seed.py``
    needs (minus ``topic_slug``, which the caller supplies because
    the auto-scraper doesn't know the topic taxonomy yet).

    Returns a dict with keys:
      ``title``, ``statement_md``, ``input_format_md``,
      ``output_format_md``, ``examples`` (list of ``(stdin, stdout)``
      pairs as raw strings, no trailing-newline guarantee),
      ``source``, ``source_url``.
    """
    parser = _ProblemParser()
    parser.feed(html)
    return {
        "title": parser.title,
        "statement_md": parser.statement,
        "input_format_md": parser.input_format,
        "output_format_md": parser.output_format,
        "examples": parser.examples,
        "source": source,
        "source_url": source_url,
    }


# ---------------------------------------------------------------------------
# HTTP — production fetcher + injected-for-tests seam
# ---------------------------------------------------------------------------


Fetcher = Callable[[str], str]


def _default_fetcher(url: str) -> str:
    """Production fetcher. One GET, 3 retries with backoff, 1s
    minimum between requests. Raises ``httpx.HTTPError`` after
    retries are exhausted so the caller can decide what to log.

    Per security-guardian: no shell, just ``httpx.get(url)`` with a
    timeout.
    """
    last_exc: Exception | None = None
    for backoff in (0.0, *RETRY_BACKOFF_SECONDS):
        if backoff:
            time.sleep(backoff)
        try:
            resp = httpx.get(url, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
            resp.raise_for_status()
            # Honor the rate limit even on success — cheap defense-in-depth.
            time.sleep(REQUEST_INTERVAL_SECONDS)
            return resp.text
        except (httpx.HTTPError, httpx.StreamError) as exc:
            last_exc = exc
            logger.warning("fetch failed: %s (retrying)", exc)
            continue
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Snapshot writing
# ---------------------------------------------------------------------------


def _today() -> dt.date:
    """Indirection so tests can freeze the date."""
    return dt.date.today()


def snapshot_filename(today: dt.date | None = None) -> Path:
    """Return the canonical snapshot path for ``today`` (defaults
    to ``dt.date.today()``). Path is ``<REPO>/data/snapshots/
    obi-YYYY-MM-DD.yaml`` — same-day re-runs overwrite."""
    today = today or _today()
    return OUTPUT_DIR / f"obi-{today.isoformat()}.yaml"


def write_snapshot(payload: dict[str, Any], path: Path) -> Path:
    """Serialise ``payload`` to YAML at ``path``. Creates parent
    dirs if needed. Same path on re-run = overwrite (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``sort_keys=False`` keeps field order: problems first, then
    # the per-problem keys in the seed-loader order. Pure readability.
    text = yaml.safe_dump(
        payload, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    path.write_text(text, encoding="utf-8")
    logger.info("wrote snapshot: %s (%d bytes)", path, path.stat().st_size)
    return path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _list_problem_slugs(
    fetcher: Fetcher, year: int, phase: str
) -> list[str]:
    """Fetch the /pratique/p1/ index and return the problem slugs
    that belong to ``(year, phase)``."""
    html = fetcher(INDEX_URL_TEMPLATE)
    parser = _IndexParser(year=year, phase=phase)
    parser.feed(html)
    return parser.slugs


def _problem_dict(
    fetcher: Fetcher,
    slug: str,
    *,
    year: int,
    phase: str,
) -> dict[str, Any] | None:
    """Fetch one problem page and return a seed-loader-shaped dict,
    or ``None`` on failure (caller logs and continues)."""
    url = PROBLEM_URL_TEMPLATE.format(year=year, phase=phase, slug=slug)
    try:
        html = fetcher(url)
    except Exception as exc:  # noqa: BLE001 — best-effort per ADR-0006
        logger.warning("skip %s: fetch failed: %s", slug, exc)
        return None
    parsed = parse_problem_page(
        html,
        source=f"OBI {year} {phase.upper()}",
        source_url=url,
    )
    if not parsed["title"]:
        logger.warning("skip %s: empty title (parse likely failed)", slug)
        return None

    # Examples -> seed-loader test_cases. Samples are the examples
    # shown on the page; weight 1 each (the original OBI scoring is
    # opaque to us and the dev can rebalance after seeding).
    test_cases: list[dict[str, Any]] = []
    for stdin, expected_stdout in parsed["examples"]:
        # Preserve trailing newlines (judges compare byte-for-byte).
        test_cases.append(
            {
                "stdin": _ensure_trailing_newline(stdin),
                "expected_stdout": _ensure_trailing_newline(expected_stdout),
                "is_sample": True,
                "weight": 1,
            }
        )

    examples_json = json.dumps(
        [
            {"stdin": s, "stdout": o, "explanation": ""}
            for s, o in parsed["examples"]
        ],
        ensure_ascii=False,
    )

    return {
        "slug": slug,
        "title": parsed["title"],
        "topic_slug": DEFAULT_TOPIC_SLUG,
        "difficulty": 1,
        "statement_md": parsed["statement_md"],
        "input_format_md": parsed["input_format_md"],
        "output_format_md": parsed["output_format_md"],
        "examples_json": examples_json,
        "source": parsed["source"],
        "source_url": parsed["source_url"],
        "test_cases": test_cases,
    }


def _ensure_trailing_newline(s: str) -> str:
    """OBI judges expect stdin/stdout to end in ``\\n``. The page
    often omits the trailing newline in its ``<pre>``; add one if
    missing. (Empty string stays empty — degenerate case.)"""
    if s and not s.endswith("\n"):
        return s + "\n"
    return s


def scrape_obi(
    *,
    fetcher: Fetcher | None = None,
    year: int = DEFAULT_YEAR,
    phase: str = DEFAULT_PHASE,
) -> SnapshotSummary:
    """Top-level entry: list slugs, fetch each problem page,
    build the seed-shaped payload, write the snapshot.

    Best-effort: failures on individual problems are logged and
    skipped. The snapshot still gets written if at least one
    problem succeeded.
    """
    _fetcher = fetcher or _default_fetcher
    slugs = _list_problem_slugs(_fetcher, year=year, phase=phase)
    logger.info(
        "OBI %s %s: %d candidate problem(s) from index",
        year,
        phase,
        len(slugs),
    )

    problems: list[dict[str, Any]] = []
    failed: list[str] = []
    for slug in slugs:
        try:
            problem = _problem_dict(
                _fetcher, slug, year=year, phase=phase
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("unexpected error on %s: %s", slug, exc)
            failed.append(slug)
            continue
        if problem is None:
            failed.append(slug)
            continue
        problems.append(problem)

    payload: dict[str, Any] = {"problems": problems}
    path = snapshot_filename()
    write_snapshot(payload, path)

    summary = SnapshotSummary(
        snapshot_path=str(path),
        problems_fetched=len(problems),
        problems_failed=len(failed),
        failed_slugs=failed,
    )
    logger.info(
        "OBI %s %s done: fetched=%d failed=%d snapshot=%s",
        year,
        phase,
        summary.problems_fetched,
        summary.problems_failed,
        path,
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Always returns 0 — per ADR-0006 best-effort,
    a partial snapshot is still a success."""
    logging.basicConfig(
        level=logging.INFO,
        format="[scrape-obi] %(levelname)s %(message)s",
    )
    scrape_obi()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
