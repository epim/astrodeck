"""astrodeck-comhost — the bundled minimal COM->Alpaca host (Windows-only).

A standalone sidecar (`python -m astrodeck.comhost`) that serves a spec-shaped
ASCOM Alpaca v1 device API on 127.0.0.1 so AstroDeck's EXISTING Alpaca client
drives installed COM ASCOM drivers unchanged (spec §3, Approach C). Import-light
by design: this package imports only stdlib + comtypes (guarded) + the
import-light ascom_registry — never the server app.
"""
