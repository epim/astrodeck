# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The relay root (``GET /``) landing page (#686): pure rendering, no sockets.

Before this, ``GET /`` 404'd -- a remote user had to already know and type the
full ``/h/<home_id>/`` path. This module is the transport-free half of the fix,
mirroring the ``headers``/``ratelimit``/``registry`` split elsewhere in this
package: given the set of CURRENTLY-CONNECTED home ids and an optional owner-set
label map, it decides between a redirect target (the common single-home case)
and a small phone-first HTML page (zero or several homes), so the Starlette
shell in ``server.py`` only has to wire this to the rate limiter and the
response headers.

Input contract (enforced by the caller, not here):
  * ``homes`` must already be ``HomeRegistry.homes()`` -- the live, sorted,
    CONNECTED home ids. A home that is merely provisioned (a token exists) but
    never dialed in must never reach this module, or it would be listed as if
    it were reachable.
  * ``labels`` is the owner-set ``{home_id: label}`` map loaded from relay
    config (``RELAY_HOME_LABELS`` / ``RELAY_HOME_LABELS_FILE``) ONLY. A label
    must never be sourced from a home's ``HELLO`` or any other tunnel frame --
    a rig's own name can carry the observing site's label, which must never
    leave the rig (site privacy rule). Nothing in this module, or in the HELLO
    handling it is fed from, reads a label out of the tunnel.

Every id and label is HTML-escaped before it reaches the page: a home id is
operator-provisioned today, but it still rides a public URL.
"""
from __future__ import annotations

from html import escape
from urllib.parse import quote

#: What the page says with no connected home. Deliberately carries no id -- a
#: provisioned-but-offline home must never be named here.
_NONE_CONNECTED_MESSAGE = "No observatory is connected right now."


def single_home_redirect_path(home_id: str) -> str:
    """The ``/h/<home_id>/`` path ``GET /`` 302s to for the one-home case.

    Percent-encodes the id (``safe=""``) so it lands inside the path segment
    unchanged by the redirect regardless of what characters a home id holds."""
    return f"/h/{quote(home_id, safe='')}/"


def render_landing_page(homes: list, labels: dict) -> str:
    """Render the ``GET /`` HTML body for the zero- or multi-home case.

    ``homes`` is used exactly as given -- already sorted, already filtered to
    live connections (see module docstring); this function does not re-derive
    either property. ``labels`` maps a connected home id to its owner-set
    display label; a home with no entry displays its own id."""
    if not homes:
        items_html = f"<p>{escape(_NONE_CONNECTED_MESSAGE)}</p>"
    else:
        rows = []
        for home_id in homes:
            label = labels.get(home_id, home_id)
            href = escape(single_home_redirect_path(home_id), quote=True)
            text = escape(str(label))
            rows.append(
                f'<li><a class="home" href="{href}">{text}</a></li>')
        items_html = '<ul class="homes">' + "".join(rows) + "</ul>"
    return _PAGE_TEMPLATE.format(items=items_html)


# A small, phone-first page: one large tap target per home, no script, and no
# styling beyond what plain inline CSS in <style> can do (the relay applies no
# CSP that would forbid it -- see server.py's _root_landing for the headers
# this page is actually served with).
_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AstroDeck</title>
<style>
  body {{
    margin: 0; padding: 1.5rem 1rem;
    font-family: system-ui, -apple-system, sans-serif;
    background: #0b0e14; color: #e8eaed;
  }}
  h1 {{ font-size: 1.1rem; font-weight: 600; margin: 0 0 1rem; }}
  ul.homes {{ list-style: none; margin: 0; padding: 0; }}
  ul.homes li {{ margin: 0 0 0.75rem; }}
  a.home {{
    display: block; padding: 1.1rem 1rem; border-radius: 0.6rem;
    background: #1b2130; color: #e8eaed; text-decoration: none;
    font-size: 1.15rem; text-align: center;
  }}
  p {{ font-size: 1rem; }}
</style>
</head>
<body>
<h1>AstroDeck</h1>
{items}
</body>
</html>
"""
