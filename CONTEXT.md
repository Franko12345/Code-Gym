# Code-Gym — Context

> The one doc loaded on every session that touches this repo. Keep
> it current.

## What this is

A **coding gym**: a playground for exercises, katas, and from-scratch
implementations. No fixed curriculum — pick a topic, build it small,
see it run.

## Goals

- **Educational**: every implementation reads top-to-bottom. No
  hidden framework magic.
- **Minimal**: standard library first; one extra dep only when it
  removes real complexity.
- **Reproducible**: each module ships with a runnable self-check
  (`python -m <module>_test` or an `assert`-based demo).

## What lives here

- **Exercises** — small algorithmic katas (sorting, search, graph,
  DP, parsing).
- **From-scratch builds** — re-implementations of canonical tools
  (regex engine, lisp evaluator, tiny DB, ray tracer, neural net
  layer, etc.).
- **Visualizers** — when a build benefits from a picture (pathfinding,
  tree balancing, gradient descent), keep it pygame or matplotlib.

## Layout

- Each exercise / build lives in its own folder or top-level module.
- Tests live next to the code as `<name>_test.py` (or under
  `tests/<name>.py` when shared fixtures help).
- Self-checks are runnable as `python3 -m <module>`.

## Status

Empty. First ticket is the engineering-skills setup (this PR).