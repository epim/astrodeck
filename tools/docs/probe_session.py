"""Owned, isolated simulator/browser console for documentation procedure review.

This tool starts a never-used configuration on a private loopback port, proves
process/listener ownership, and always stops its server on normal console exit.
It intentionally uses --no-sim so the documented simulator connection is clicked
in the UI. No application state or HTTP response is fabricated.
"""
from __future__ import annotations
import argparse
import code
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/site"))
import capture_sim as ownership


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, default=8892)
    args = parser.parse_args()
    if not args.name.replace("-", "").isalnum():
        raise ValueError("Use a simple private run name")
    config, captures = ownership.fresh_paths(ROOT / ".probe/docs" / args.name, args.port)
    python = Path(sys.executable).resolve()
    browser_cache = ROOT / ".probe/docs/browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    scanner = runpy.run_path(str(ROOT / "tools/privacy_scan.py"))
    needles = scanner["load_needles"]()
    if not needles:
        raise RuntimeError("Configure the external privacy needles before running procedures")
    patterns = scanner["patterns_for"](needles)
    launcher = ROOT / "tools/ui_probe/server_ctl.py"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "server"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", args.port))
    command = [str(python), str(launcher), "start", "--fresh", "--no-sim",
               "--port", str(args.port), "--config-dir", str(config),
               "--capture-dir", str(captures), "--ui-dir", str(ROOT / "ui/dist"),
               "--venv-python", str(python)]
    started = time.time()
    records = []
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Owned simulator launch failed; inspect private probe logs")
        ownership.owned_listener(config, args.port, python, started)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            base = f"http://127.0.0.1:{args.port}"
            request = context.request
            page = context.new_page()

            def route_guard(route):
                url = route.request.url
                if (url.startswith(base + "/") and "/api/survey/" not in url and "/api/hips/" not in url
                        or url.startswith(("data:", "blob:"))):
                    route.continue_()
                else:
                    route.abort()
            page.route("**/*", route_guard)

            def emit(value):
                text = json.dumps(value, ensure_ascii=False, indent=2)
                if scanner["scan_text"](text, patterns):
                    raise RuntimeError("Private value blocked from procedure output")
                print(text, flush=True)

            def controls():
                emit(page.locator("button").all_text_contents())

            def fields():
                emit(page.locator("input,select,textarea").evaluate_all(
                    "(xs)=>xs.map(x=>({tag:x.tagName,type:x.type,placeholder:x.placeholder,"
                    "aria:x.getAttribute('aria-label'),id:x.id,value:x.value}))"))

            def record(step, observed):
                ownership.owned_listener(config, args.port, python, started)
                row = {"step": step, "observed": observed, "route": page.url.split("#")[-1]}
                emit(row)
                records.append(row)
                (config / "procedure-record.json").write_text(json.dumps({
                    "source_commit": subprocess.check_output(["git","rev-parse","HEAD"], cwd=ROOT, text=True).strip(),
                    "run": args.name, "port": args.port, "fresh_config": True,
                    "browser_external_requests": "blocked; survey/HiPS requests blocked as well",
                    "records": records,
                }, indent=2) + "\n", encoding="utf-8")

            page.goto(base + "/#/next", wait_until="domcontentloaded")
            page.get_by_test_id("next-app").wait_for()
            page.wait_for_timeout(1200)
            print("Owned procedure console ready. Helpers: controls(), fields(), record(step, observed).", flush=True)
            code.interact(banner="", local={
                "page": page, "context": context, "browser": browser, "request": request,
                "base": base, "ROOT": ROOT, "config": config, "records": records,
                "controls": controls, "fields": fields, "emit": emit, "record": record,
                "ownership": ownership, "time": time, "json": json,
            })
            browser.close()
    finally:
        stopped = False
        try:
            if (config / ".astrodeck-probe").is_file():
                if ownership.owned_process(config, args.port, python, started) is not None:
                    subprocess.run([str(python), str(launcher), "stop", "--config-dir", str(config)],
                                   env=env, capture_output=True, text=True, check=True)
                    stopped = True
        except Exception:
            print("Procedure cleanup incomplete; inspect the private probe state.", flush=True)
            raise
        else:
            print("Owned procedure server stopped." if stopped else "No owned process was stopped.", flush=True)


if __name__ == "__main__":
    main()
