# ADR-0004 — HTMX + CodeMirror boundary

- **Status:** accepted
- **Date:** 2026-08-09

## Context

The frontend is built with HTMX 2.x: HTML over the wire, no SPA, no
build step. That works perfectly for navigation, forms, and partial
updates.

But the problem page needs:
- A syntax-highlighted code editor
- Language switching (C++ ↔ Python)
- Per-keystroke autosave to localStorage
- Real-time "running…" indicator

These are inherently client-side interactions. HTMX does not do them
well.

## Decision

**HTMX is the default for all pages and partials.** The problem page
(`/problems/{slug}`) is the only place with non-HTMX JS, and that JS
is **only CodeMirror 6 + a small localStorage adapter**.

```html
<!-- /problems/{slug} page -->
<!-- SRI: TODO add sha384 hash when picking exact CDN bundle before M4.T5 -->
<script src="https://cdn.jsdelivr.net/npm/codemirror@6/.../codemirror.min.js"
        integrity="" crossorigin="anonymous"></script>
<script>
  // 1. Init CodeMirror with mode matching server-rendered language
  // 2. Load saved code from localStorage if present
  // 3. On keyup (debounced 500ms), save to localStorage
  // 4. On language select change, swap mode + clear storage for new lang
  // 5. On form submit, copy CM content into <textarea name="code">
</script>
```

Server-side, the form submission still uses standard HTTP POST —
the JS just copies the editor buffer into the form field before
submit. No XHR/fetch interception.

The boundary: **HTMX drives navigation and partial swaps. Vanilla JS
+ CodeMirror drives only the editor.** No JS framework. No React, no
Vue, no Svelte.

## Consequences

- **Positive:** stays within "no build step" — CodeMirror loads from CDN.
- **Positive:** keeps HTMX's mental model intact everywhere else.
- **Positive:** the JS surface is tiny — ~50 lines for the whole
  editor behavior.
- **Negative:** the problem page is the one place where disabling JS
  breaks the UX (no syntax highlight, no autosave). Acceptable — it's
  still functional (basic textarea).
- **Reversibility:** high. Swapping CodeMirror for Monaco is a JS
  file change.

## Alternatives considered

- **Full SPA (React/Svelte/SvelteKit):** rejected — kills the
  "no build step" property.
- **HTMX-only, no syntax highlight:** degraded UX for a tool whose
  whole point is editing code. Rejected.
- **Static `<textarea>` only, syntax highlight via server-side
  highlight.js on render:** possible but live editing benefits from
  in-browser highlight.