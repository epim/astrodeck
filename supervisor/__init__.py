"""AstroDeck supervisor: a thin, standalone launcher that supervises the server
process and applies staged self-updates with health-check + rollback.

Standalone (stdlib only) and single-binary-ready: the natural first component to
port to the future Rust/Go engine. See docs/superpowers/specs/2026-06-19-self-update-design.md.
"""
