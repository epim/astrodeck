"""Self-update subsystem (spec: docs/superpowers/specs/2026-06-19-self-update-design.md).

Import-light by design: the version/health endpoints (Phase 1) import only
``version``/``signing``/``state`` from here; the heavier service (poller +
download + stage) lives in ``service`` and is started opt-in from the app
lifespan.
"""
