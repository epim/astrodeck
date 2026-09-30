"""uvicorn log formatting for the rig server: the path, never the query (#550).

uvicorn's stock access line is `GET /api/cloudmap/at?alt=..&az=.. HTTP/1.1`, and
its error logger writes the same full target for a WebSocket handshake
(`"WebSocket /ws?..." [accepted]`). The relay got a path-only formatter for
this in #520 (`relay/relay/server.py`), after its own log held a live mount
pointing several times a minute. This module gives the rig's own uvicorn the
same treatment. It mirrors the relay's formatters on purpose -- the rig does
not import `relay`, so a shared bug class needs its own fix and its own test
here rather than a dependency on the other process's package.

`ASTRODECK_TOKEN` (the shared admin credential, see `api/app.py:_present_token`)
is accepted as a query parameter, and a request that used it would otherwise
put the credential in this log. Withholding is unconditional -- the query
string is dropped whatever it carries, so a route added later is covered
without this file changing.
"""
from __future__ import annotations

import copy
import logging

from uvicorn.logging import AccessFormatter as _AccessFormatter
from uvicorn.logging import DefaultFormatter as _DefaultFormatter

#: What a withheld query string reads as. No space in it: the request line is
#: split on spaces by anything that parses an access log.
QUERY_WITHHELD = "?<withheld>"


def _path_only(value: object) -> object:
    """A request target with its query string replaced by a marker. Anything
    that is not a path carrying a query comes back untouched, so the same rule
    can be run over every argument of every uvicorn record."""
    if isinstance(value, str) and value.startswith("/") and "?" in value:
        return value.split("?", 1)[0] + QUERY_WITHHELD
    return value


def _without_queries(record: logging.LogRecord) -> logging.LogRecord:
    """A copy of ``record`` whose request targets have lost their queries.

    A COPY, because the record is shared: every handler on the logger and its
    parents is handed the same object, and rewriting it in place would change
    what they see as a side effect of which formatter happened to run first."""
    args = record.args
    if not isinstance(args, tuple) or not any(
            _path_only(a) is not a for a in args):
        return record
    clone = copy.copy(record)
    clone.args = tuple(_path_only(a) for a in args)
    return clone


class PathOnlyAccessFormatter(_AccessFormatter):
    """uvicorn's access formatter, with the query string withheld."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(_without_queries(record))


class PathOnlyDefaultFormatter(_DefaultFormatter):
    """uvicorn's default formatter (its error logger, which is where the
    WebSocket handshake lines go), with the query string withheld."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(_without_queries(record))


def uvicorn_log_config() -> dict:
    """uvicorn's own logging config with both formatters swapped for the
    path-only ones. Copied, not edited: ``LOGGING_CONFIG`` is uvicorn's module
    global, and uvicorn writes into the dict it is handed."""
    from uvicorn.config import LOGGING_CONFIG

    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["formatters"]["access"]["()"] = PathOnlyAccessFormatter
    cfg["formatters"]["default"]["()"] = PathOnlyDefaultFormatter
    return cfg
