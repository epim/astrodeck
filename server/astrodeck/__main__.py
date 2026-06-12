"""Run the AstroDeck server: python -m astrodeck [--port 8800]"""
from __future__ import annotations

import argparse

import uvicorn

from .api import create_app

app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(prog="astrodeck")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()
    uvicorn.run("astrodeck.__main__:app", host=args.host, port=args.port,
                log_level="info")


if __name__ == "__main__":
    main()
