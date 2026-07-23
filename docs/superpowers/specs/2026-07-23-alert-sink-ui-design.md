# PRO-9 — Alert-sink config UI + Discord / email / Slack channels (pro)

Combined design spec + TDD implementation plan. One file.

---

## 1. Design

### 1.1 Goal

Two deliverables:

* **(a) A Settings → Alerts panel** to add / edit / test / delete outbound alert
  sinks, and to show per-sink health (verified badge, undelivered-queue depth,
  and the dead-man's-switch state).
* **(b) Three new native channel adapters** — **Discord**, **SMTP email**, and
  **Slack** — on the existing `AlertDispatcher` sink interface, each with pytest
  coverage.

The alert *dispatcher* is already complete and hardened. This feature adds the
missing UI surface and the three missing channels only.

### 1.2 Current-state seams (every claim is a real file:line I read)

**The dispatcher is done.** `server/astrodeck/alerting.py` is a 535-line
`AlertDispatcher`:

* Sink dispatch by kind — `_send()` at `alerting.py:353-367` fans to
  `_send_ntfy` (`:378-391`), `_send_webhook` (`:393-400`), `_send_telegram`
  (`:402-409`), else returns `False, "unknown sink kind …"` (`:363`). This is the
  exact seam the three new adapters plug into.
* Success test — `_ok()` at `alerting.py:411-415` (2xx ⇒ `(True, None)`).
* Secret scrub for error/log strings — `_scrub()` at `alerting.py:369-376`
  (replaces `sink.token` with `***`).
* SSRF guard — `_url_is_safe()` at `alerting.py:69-103` (http(s) scheme, rejects
  internal IP literals; `allow_private` only for the deadman).
* Dedupe / retry / dead-man / heartbeat — never-dedupe set `:40`, undelivered
  deque `:146`, `undelivered_count` property `:434-436`, deadman state
  `_last_deadman` `:153` + `_deadman_warned` `:157`, `deadman_ping()`
  `:440-503`.
* Round-trip test — `test()` at `alerting.py:521-534` sends a real
  `AlertEvent("test", …)` through `_send()` and sets `sink.verified` only on a
  genuine `ok`.

**The sink model + secret plumbing is done.**
`server/astrodeck/config.py`:

* `AlertSink` dataclass — `config.py:140-150`: `id, kind, enabled, url, token,
  chat_id, min_level, events, verified, heartbeat_min`. `kind` is a bare `str`
  (no enum/validator), so new kinds need no model-level allowlist change.
* `AppConfig.alerts` `config.py:418`, `deadman_url` `:419`.
* `ConfigStore.set_alerts` `config.py:656-659`, `set_deadman` `:661-664`.
* `redacted()` `config.py:1006-1036`: blanks each sink's `token` (`:1021-1022`)
  and `chat_id` (`:1025-1026`), strips url userinfo (`:1029-1030`), and blanks
  `deadman_url` while surfacing `deadman_configured` (`:1034-1036`). **The token
  is the single at-rest secret and is already blanked outbound.**

**The CRUD + test API is done** (the brief says "only settable by hand-editing
config" — that is *stale*; the endpoints exist). `server/astrodeck/api/app.py`:

* `dispatcher = AlertDispatcher(bus, …)` at `app.py:84`.
* `GET /api/alerts` `app.py:1907-1915` (tokens blanked), `POST /api/alerts`
  upsert `:1917-1941`, `DELETE /api/alerts/{id}` `:1943-1949`,
  `POST /api/alerts/{id}/test` `:1951-1965`.
* Verified-reset-on-identity-change + "empty token means unchanged" merge —
  `_merge_alert_verified` `app.py:1521-1542` and the mirror inside `upsert_alert`
  `:1928-1934`. **The identity-change tuple is `url/token/chat_id/kind`** (`:1538`,
  `:1928-1929`) — it does **not** yet include the new SMTP fields.
* Cap is `CAP_CONFIG_ALERTS` (imported `app.py:28`; mapped for
  `alerts`/`deadman_url` at `:1596-1597`); read endpoints use `CAP_VIEW_STATUS`
  (`:1907`).

**The UI has no alert surface at all.**

* `ui/src/types.ts:751-761` already declares `AlertSink` (no `token` — read-only),
  and `config.alerts` / `deadman_url` / `deadman_configured` at `:526/:531/:532`;
  the `"config.alerts"` capability string exists at `:1149`.
* `ui/src/lib/caps.ts` lists `config.alerts` in `ALL_CAPS` (`:90-91`); hooks like
  `useCanConfigBackend = () => useCapability("config.backend")` at `:218` are the
  pattern for a `useCan("config.alerts")` gate.
* `ui/src/api/` has **no** `alerts.ts` — only `backends.ts / sessions.ts /
  site.ts / weather.ts`. No `listAlerts / upsertAlert / testAlert` anywhere
  (grep clean).
* `ui/src/components/settings/SettingsView.tsx` — tab list `:62-81`, tab bodies
  `:134-186`. **No Alerts tab.** New panels mount here exactly like
  `WeatherPanel` (`:155`) / `SafetyPanel` (`:135`).

### 1.3 Approach

#### Backend — the three channels reuse the *single-secret* model

The clean, low-risk move for a hardened codebase is to keep **`token` as the one
per-sink secret** for every kind, so the existing `redacted()` blank + the
"empty token means unchanged" merge (`app.py:1533-1536`, `:1931-1934`) cover the
new channels with **zero change** to that machinery:

| kind       | secret in `token`                     | non-secret fields              | transport |
|------------|---------------------------------------|--------------------------------|-----------|
| `discord`  | the full Discord **webhook URL**      | —                              | httpx POST `{content}` |
| `slack`    | the full Slack **incoming-webhook URL** | —                            | httpx POST `{text}` |
| `email`    | the **SMTP password**                 | `smtp_host/port/user/from/to/starttls` | `smtplib` in a thread |

Why the webhook URL lives in `token`, not `url`: a Discord/Slack webhook URL is a
**bearer secret** (anyone holding it can post). `url` is kept for the *non-secret*
endpoints (ntfy topic, user's own webhook), which `redacted()` deliberately keeps
visible (`config.py:1027-1030`). Routing the Discord/Slack secret through `token`
means it is blanked outbound and round-trips as "unchanged" for free, and
`_scrub()` (`alerting.py:369-376`) already strips it from any httpx error string
(the error stringifies the request URL = the token).

Adapters slot into `_send()` (`alerting.py:356-363`) and reuse `_url_is_safe`
(discord/slack — both resolve to public hosts, so the SSRF block stays ON) and
`_ok`. Email is `smtplib` (blocking) wrapped in `asyncio.to_thread`, returning a
`(ok, err)` tuple so it never raises out of `_send`; STARTTLS + optional
`login()`. Email has **no** `_url_is_safe` guard — an SMTP smarthost is a
`host:port`, not an http URL, and an internal LAN relay is a legitimate,
common setup (same rationale as the deadman's `allow_private`).

**Health surfacing.** `verified` is already per-sink in the redacted config, so
the panel reads it straight from `config.alerts[].verified`. The *runtime* health
(retry-queue depth, deadman state) is dispatcher memory, not config — add a pure
`AlertDispatcher.health()` read + a `GET /api/alerts/health` route (view-gated).

**A per-sink "is the secret set?" marker.** The client never sees `token`, so it
can't tell a configured Discord/Slack/email sink from an empty one. Mirror the
`astrospheric_configured` / `deadman_configured` precedent
(`config.py:1036`, `weather` write-only key): expose a derived
`token_configured: bool` on each redacted sink dict (in `redacted()` and
`list_alerts`). It is a serialization-only marker, never a persisted model field.

#### UI — pure logic behind a tested module; thin render on top

* **`ui/src/lib/alertSinks.ts`** (new, pure, `tsx`-tested): `ALERT_KINDS`,
  `ALL_EVENTS`, `kindLabel`, `defaultDraft`, `validateDraft` (per-kind required
  fields), `deriveSinkHealth` (folds `enabled` + queue + `verified` into a
  tone/label verdict), `deadmanVerdict`. This is the load-bearing logic and the
  only real test target.
* **`ui/src/api/alerts.ts`** (new): thin `listAlerts / upsertAlert / deleteAlert /
  testAlert / getAlertHealth` over the `api` wrapper (`ui/src/api.ts:90-95`).
* **`ui/src/components/settings/AlertsPanel.tsx`** (new): the panel. Draft-seed /
  dirty / save idiom copied from `WeatherPanel.tsx:30-107`; write-only secret
  input copied from the Astrospheric key field (`WeatherPanel.tsx:155-177`);
  read-only lock note from `DriversPanel.tsx:434-439`. Toasts via
  `useStore.getState().enqueueToast` (per constraints).
* **`ui/src/components/settings/SettingsView.tsx`**: add an `"alerts"` tab (shown
  when `useCan("config.alerts")`), mount `<AlertsPanel/>` — mirror the existing
  `WeatherPanel`/`SafetyPanel` mounts (`SettingsView.tsx:135`, `:155`).

### 1.4 Placement

* Backend channels + `health()` → `server/astrodeck/alerting.py`.
* SMTP model fields + `token_configured` in `redacted()` → `server/astrodeck/config.py`.
* `token_configured` in `list_alerts`, SMTP fields in the identity-change checks,
  new `GET /api/alerts/health` → `server/astrodeck/api/app.py`.
* Types + client + pure lib + panel + tab → `ui/src/`.

---

## 2. Global Constraints (verbatim, binding)

* **Privacy.** The real coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  `"[SITE-LABEL]"` must **NEVER** appear in code, tests, or docs. The site default
  is `"My Observatory"` / `0.0`. (No coordinates appear anywhere in this feature.)
* **Never `git add -A`.** Stage explicit paths only.
* **UI typecheck gate:** `cd ui && npx tsc -b`.
* **No jsdom / DOM harness.** Pure logic is tested with `npx tsx` inline-assert
  files. Idioms:
  `ui/src/components/ui/__tests__/SegmentedControl.test.tsx`,
  `ui/src/components/__tests__/healthStrip.test.ts`,
  `ui/src/lib/__tests__/eta.test.ts`.
* **Backend tests:** `server/.venv/Scripts/pytest.exe` (run from repo root; `-n0`
  for a single test).
* **Client toasts:** `useStore.getState().enqueueToast`.
* **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title` — never native `disabled`.
* **Do not disrupt astrotown.**

---

## 3. TDD Plan

Six tasks. Every task lists exact Files, an Interfaces block, and bite-sized
TDD steps with real code + the exact command and expected output.

Task order: **B1 → B2 → B3** (backend) may run in parallel with **U1 → U2**
(UI types/lib); **U3** depends on U1+U2 and (for live health) B3.

---

### Task B1 — SMTP model fields + `token_configured` marker + identity-change extension

Config-shape + secret plumbing. No behavior yet.

**Files**
* Modify: `server/astrodeck/config.py` (`AlertSink` `:140-150`; `redacted()`
  `:1017-1030`).
* Modify: `server/astrodeck/api/app.py` (`list_alerts` `:1912-1915`;
  `_merge_alert_verified` `:1537-1540`; `upsert_alert` `:1928-1930`).
* Test: `server/tests/test_alerting.py` (append).

**Interfaces**

```python
# config.py — appended to AlertSink (additive; old configs load fine)
class AlertSink(BaseModel):
    ...  # existing fields unchanged
    # --- email (SMTP) channel (PRO-9). The SMTP password rides in `token`
    #     (the single per-sink secret). These are NON-secret and stay visible.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""          # login user (usually the From address)
    smtp_from: str = ""          # From / envelope address
    smtp_to: str = ""            # comma-separated recipients
    smtp_starttls: bool = True   # STARTTLS after connect (587); False = plain
```

**Steps**

1. **Add the six SMTP fields** to `AlertSink` (`config.py:150`, right after
   `heartbeat_min`). All defaulted ⇒ additive.

2. **Expose `token_configured` in `redacted()`.** Inside the sink loop
   (`config.py:1017-1030`), before blanking `token`, record the marker:

   ```python
   for sink in data.get("alerts", []):
       if not isinstance(sink, dict):
           continue
       sink["token_configured"] = bool(sink.get("token"))  # derived marker
       if sink.get("token"):
           sink["token"] = ""
       ...  # existing chat_id + url scrub unchanged
   ```

3. **Expose it in `list_alerts`** (`app.py:1912-1915`) — that route builds dicts
   directly, so inject the marker there too:

   ```python
   return [
       {**s.model_copy(update={"token": ""}).model_dump(),
        "token_configured": bool(s.token)}
       for s in config_store.cfg().alerts
   ]
   ```
   (Apply the same `{**dump, "token_configured": bool(s.token)}` shape to the
   two return statements in `upsert_alert` at `app.py:1940-1941`.)

4. **Extend the identity-change tuple** so a re-pointed *email* sink drops its
   `verified` badge (Discord/Slack are already covered by the `token` term). In
   both `_merge_alert_verified` (`app.py:1537-1539`) and `upsert_alert`
   (`app.py:1928-1929`), add the SMTP fields:

   ```python
   if old is not None and (
           old.url != sink.url or old.token != sink.token
           or old.chat_id != sink.chat_id or old.kind != sink.kind
           or old.smtp_host != sink.smtp_host or old.smtp_port != sink.smtp_port
           or old.smtp_user != sink.smtp_user or old.smtp_from != sink.smtp_from
           or old.smtp_to != sink.smtp_to):
       sink = sink.model_copy(update={"verified": False})
   ```

5. **Test** (append to `server/tests/test_alerting.py`):

   ```python
   from astrodeck.config import AppConfig, AlertSink, redacted

   def test_redacted_marks_token_configured_and_blanks_secret():
       cfg = AppConfig(alerts=[
           AlertSink(id="d", kind="discord", token="https://discord.com/api/webhooks/1/xyz"),
           AlertSink(id="e", kind="email", smtp_host="smtp.x", smtp_from="a@x",
                     smtp_to="b@x", token="pw"),
           AlertSink(id="n", kind="ntfy", url="https://ntfy.sh/t"),
       ])
       out = redacted(cfg)
       by = {s["id"]: s for s in out["alerts"]}
       assert by["d"]["token"] == "" and by["d"]["token_configured"] is True
       assert by["e"]["token"] == "" and by["e"]["token_configured"] is True
       # non-secret SMTP fields stay visible
       assert by["e"]["smtp_host"] == "smtp.x" and by["e"]["smtp_to"] == "b@x"
       assert by["n"]["token_configured"] is False  # no secret set
   ```

   Command / expected:
   ```
   server/.venv/Scripts/pytest.exe -n0 server/tests/test_alerting.py::test_redacted_marks_token_configured_and_blanks_secret -q
   # 1 passed
   ```

**Impl tier:** Sonnet — additive fields + a derived dict key + a widened tuple,
all against cited lines.

---

### Task B2 — Discord / Slack / email channel adapters

**Files**
* Modify: `server/astrodeck/alerting.py` (`_send` `:356-363`; add three adapters
  + one module helper).
* Test: `server/tests/test_alerting.py` (append; reuse the `_dispatcher`
  MockTransport fixture at `test_alerting.py:19-24`).

**Interfaces**

```python
# alerting.py
async def _send_discord(self, client, sink, ev) -> tuple[bool, str | None]
async def _send_slack(self, client, sink, ev) -> tuple[bool, str | None]
async def _send_email(self, sink, ev) -> tuple[bool, str | None]

def _smtp_send_blocking(sink, ev) -> tuple[bool, str | None]  # module-level; runs in a thread
```

**Steps**

1. **Wire the three kinds into `_send`** (`alerting.py:356-363`), before the
   `unknown sink kind` fallthrough:

   ```python
   if sink.kind == "discord":
       return await self._send_discord(client, sink, ev)
   if sink.kind == "slack":
       return await self._send_slack(client, sink, ev)
   if sink.kind == "email":
       return await self._send_email(sink, ev)
   ```

2. **Discord** (webhook URL is the bearer secret in `token`):

   ```python
   async def _send_discord(self, client, sink, ev):
       url = sink.token  # the whole webhook URL is a bearer secret -> stored in token
       if not url:
           return False, "no discord webhook url"
       if not _url_is_safe(url):
           return False, "blocked discord url (require http(s); no internal host)"
       content = f"**AstroDeck: {ev.type}** — {ev.message}"
       r = await client.post(url, json={"content": content[:1900]})
       return self._ok(r)  # Discord returns 204 on success (within 2xx)
   ```

3. **Slack**:

   ```python
   async def _send_slack(self, client, sink, ev):
       url = sink.token
       if not url:
           return False, "no slack webhook url"
       if not _url_is_safe(url):
           return False, "blocked slack url (require http(s); no internal host)"
       r = await client.post(url, json={"text": f"AstroDeck [{ev.type}] {ev.message}"})
       return self._ok(r)
   ```

4. **Email** — blocking `smtplib` in a thread, never raises, secret scrubbed:

   ```python
   async def _send_email(self, sink, ev):
       if not (sink.smtp_host and sink.smtp_from and sink.smtp_to):
           return False, "email needs smtp host, from, and to"
       ok, err = await asyncio.to_thread(_smtp_send_blocking, sink, ev)
       return ok, (self._scrub(err, sink) if err else None)
   ```

   Module-level helper (top level of `alerting.py`, near the other helpers):

   ```python
   def _smtp_send_blocking(sink, ev):
       """Blocking SMTP send (runs in a worker thread). Returns (ok, err);
       never raises. STARTTLS + optional login; password is sink.token."""
       import smtplib
       from email.message import EmailMessage
       recipients = [a.strip() for a in sink.smtp_to.split(",") if a.strip()]
       if not recipients:
           return False, "email has no valid recipients"
       msg = EmailMessage()
       msg["Subject"] = f"AstroDeck: {ev.type}"
       msg["From"] = sink.smtp_from
       msg["To"] = ", ".join(recipients)
       msg.set_content(ev.message)
       try:
           with smtplib.SMTP(sink.smtp_host, sink.smtp_port or 587,
                             timeout=_HTTP_TIMEOUT_S) as s:
               if sink.smtp_starttls:
                   s.starttls()
               if sink.smtp_user and sink.token:
                   s.login(sink.smtp_user, sink.token)
               s.send_message(msg, to_addrs=recipients)
           return True, None
       except (OSError, smtplib.SMTPException) as e:
           return False, f"{type(e).__name__}: {e}"
   ```

5. **Tests** (append; mirror `test_ntfy_webhook_telegram_send_ok` at
   `test_alerting.py:29-50`):

   ```python
   async def test_discord_and_slack_post_to_token_webhook():
       seen = []
       def handler(req):
           seen.append((str(req.url), req.read().decode()))
           return httpx.Response(204)
       cfg = AppConfig(alerts=[
           AlertSink(id="d", kind="discord",
                     token="https://discord.com/api/webhooks/1/xyz",
                     events=["run_end"], min_level="warning"),
           AlertSink(id="s", kind="slack",
                     token="https://hooks.slack.com/services/T/B/xyz",
                     events=["run_end"], min_level="warning"),
       ])
       disp, _bus = _dispatcher(cfg, handler)
       await disp._dispatch(AlertEvent("run_end", "error", "Run aborted"))
       urls = [u for u, _ in seen]
       assert any("discord.com/api/webhooks" in u for u in urls)
       assert any("hooks.slack.com/services" in u for u in urls)
       assert any('"content"' in b for _, b in seen)  # discord shape
       assert any('"text"' in b for _, b in seen)     # slack shape
       await disp._client.aclose()

   async def test_discord_slack_block_internal_host():
       disp, _bus = _dispatcher(AppConfig(), lambda r: httpx.Response(204))
       ok, err = await disp._send(
           AlertSink(id="d", kind="discord", token="http://192.168.1.5/hook"),
           AlertEvent("run_end", "error", "x"))
       assert ok is False and "blocked" in (err or "")
       await disp._client.aclose()

   async def test_email_missing_fields_is_soft_error():
       disp, _bus = _dispatcher(AppConfig(), lambda r: httpx.Response(200))
       ok, err = await disp._send(
           AlertSink(id="e", kind="email", smtp_host="", smtp_from="", smtp_to=""),
           AlertEvent("run_end", "error", "x"))
       assert ok is False and "email needs" in (err or "")  # never raised
       await disp._client.aclose()

   async def test_email_send_ok_via_monkeypatched_smtp(monkeypatch):
       sent = {}
       class FakeSMTP:
           def __init__(self, host, port, timeout=None): sent["addr"] = (host, port)
           def __enter__(self): return self
           def __exit__(self, *a): return False
           def starttls(self): sent["tls"] = True
           def login(self, u, p): sent["login"] = (u, p)
           def send_message(self, msg, to_addrs=None): sent["to"] = to_addrs
       import astrodeck.alerting as A
       monkeypatch.setattr(A.smtplib, "SMTP", FakeSMTP) if hasattr(A, "smtplib") else None
       monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
       cfg = AppConfig(alerts=[AlertSink(id="e", kind="email", smtp_host="smtp.x",
                       smtp_port=587, smtp_user="a@x", token="pw",
                       smtp_from="a@x", smtp_to="b@x, c@x")])
       disp, _bus = _dispatcher(cfg, lambda r: httpx.Response(200))
       ok, err = await disp._send(cfg.alerts[0], AlertEvent("test", "info", "hi"))
       assert ok is True and err is None
       assert sent["addr"] == ("smtp.x", 587) and sent["tls"] is True
       assert sent["login"] == ("a@x", "pw") and sent["to"] == ["b@x", "c@x"]
       await disp._client.aclose()
   ```

   Command / expected:
   ```
   server/.venv/Scripts/pytest.exe -n0 server/tests/test_alerting.py -q
   # all pass (existing + 4 new)
   ```

**Impl tier:** Sonnet — the adapters follow the existing `_send_*` shape exactly,
and the one subtle piece (SMTP in a thread, never-raise, STARTTLS/login order) is
fully specified above. No novel algorithm.

---

### Task B3 — `health()` snapshot + `GET /api/alerts/health`

**Files**
* Modify: `server/astrodeck/alerting.py` (add `health()` near `undelivered_count`
  `:434-436`).
* Modify: `server/astrodeck/api/app.py` (add route after `test_alert` `:1965`).
* Test: `server/tests/test_alerting.py` (append).

**Interfaces**

```python
# alerting.py
def health(self) -> dict[str, Any]:
    """Pure read of in-memory dispatcher state (no I/O): retry-queue depth
    (global + per-sink) and dead-man's-switch state, for the settings panel."""
```

Wire shape (`GET /api/alerts/health`, CAP_VIEW_STATUS):
```json
{
  "undelivered": 0,
  "undelivered_by_sink": {"sink-id": 2},
  "deadman": {"configured": true, "healthy": true, "last_ping_age_s": 12.4}
}
```

**Steps**

1. **`health()`** on `AlertDispatcher`:

   ```python
   def health(self) -> dict[str, Any]:
       by_sink: dict[str, int] = {}
       for sink, _ev in self._undelivered:
           by_sink[sink.id] = by_sink.get(sink.id, 0) + 1
       dm_url = getattr(self.get_config(), "deadman_url", "") or ""
       last_age = (time.monotonic() - self._last_deadman
                   if self._last_deadman else None)
       return {
           "undelivered": len(self._undelivered),
           "undelivered_by_sink": by_sink,
           "deadman": {
               "configured": bool(dm_url),
               "healthy": bool(dm_url) and self._deadman_warned is None,
               "last_ping_age_s": last_age,
           },
       }
   ```

2. **Route** (`app.py`, after `test_alert` at `:1965`) — view-gated like
   `list_alerts` (`:1907`):

   ```python
   @app.get("/api/alerts/health", dependencies=[Depends(require(CAP_VIEW_STATUS))])
   @declare(CAP_VIEW_STATUS)
   async def alerts_health():
       """Dispatcher runtime health (queue depth + dead-man state) for the
       Settings → Alerts panel. Pure read; never does I/O."""
       return dispatcher.health()
   ```

3. **Test** (append):

   ```python
   async def test_health_reports_queue_and_deadman():
       cfg = AppConfig(deadman_url="https://hc-ping.com/abc",
                       alerts=[AlertSink(id="n", kind="ntfy", url="https://x/y",
                                         events=["run_end"], min_level="warning")])
       disp, _bus = _dispatcher(cfg, lambda r: httpx.ConnectError("offline"))
       await disp._dispatch(AlertEvent("run_end", "error", "boom"))  # fails -> queued
       h = disp.health()
       assert h["undelivered"] == 1 and h["undelivered_by_sink"]["n"] == 1
       assert h["deadman"]["configured"] is True
       assert h["deadman"]["healthy"] is True   # not yet warned
       await disp._client.aclose()
   ```

   Command / expected:
   ```
   server/.venv/Scripts/pytest.exe -n0 server/tests/test_alerting.py::test_health_reports_queue_and_deadman -q
   # 1 passed
   ```

**Impl tier:** Sonnet — a pure dict read + a one-line view-gated route mirroring
`list_alerts`.

---

### Task U1 — UI types + typed API client

**Files**
* Modify: `ui/src/types.ts` (extend `AlertSink` `:751-761`; add `AlertHealth`,
  `AlertSinkInput`).
* Create: `ui/src/api/alerts.ts`.

**Interfaces**

```typescript
// types.ts — extend AlertSink (read shape; token never present)
export interface AlertSink {
  id: string;
  kind: "ntfy" | "webhook" | "telegram" | "discord" | "slack" | "email";
  enabled: boolean;
  url: string;
  chat_id?: string;
  min_level: "warning" | "error";
  events: string[];
  verified: boolean;
  heartbeat_min: number;
  token_configured?: boolean;   // derived server marker: is the secret set?
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_from?: string;
  smtp_to?: string;
  smtp_starttls?: boolean;
}

// write body: adds the write-only secret
export interface AlertSinkInput extends AlertSink {
  token?: string;   // telegram bot token | discord/slack webhook url | smtp password
}

export interface AlertHealth {
  undelivered: number;
  undelivered_by_sink: Record<string, number>;
  deadman: { configured: boolean; healthy: boolean; last_ping_age_s: number | null };
}
```

```typescript
// api/alerts.ts
import { api } from "../api";
import type { AlertSink, AlertSinkInput, AlertHealth } from "../types";

export const listAlerts = (): Promise<AlertSink[]> => api.get<AlertSink[]>("/api/alerts");
export const upsertAlert = (sink: AlertSinkInput): Promise<AlertSink[]> =>
  api.post<AlertSink[]>("/api/alerts", sink);
export const deleteAlert = (id: string): Promise<{ deleted: string }> =>
  api.del<{ deleted: string }>(`/api/alerts/${encodeURIComponent(id)}`);
export const testAlert = (id: string): Promise<{ ok: boolean; error?: string; verified: boolean }> =>
  api.post(`/api/alerts/${encodeURIComponent(id)}/test`, {});
export const getAlertHealth = (): Promise<AlertHealth> => api.get<AlertHealth>("/api/alerts/health");
```

**Steps**

1. Edit `types.ts:751-761` — widen the `kind` union and append the optional
   fields shown above; add `AlertHealth` + `AlertSinkInput` after the interface.
2. Create `ui/src/api/alerts.ts` exactly as above (methods verified present at
   `ui/src/api.ts:91-95`).
3. Gate: `cd ui && npx tsc -b` ⇒ **no errors**.

**Impl tier:** Sonnet — type widening + a five-line client over the shared
wrapper.

---

### Task U2 — pure `alertSinks` logic + `tsx` test

The load-bearing logic. Tested; the panel binds to it.

**Files**
* Create: `ui/src/lib/alertSinks.ts`.
* Create: `ui/src/lib/__tests__/alertSinks.test.ts` (inline-assert, per
  `eta.test.ts`).

**Interfaces**

```typescript
// lib/alertSinks.ts
import type { AlertSink, AlertSinkInput, AlertHealth } from "../types";

export type AlertKind = AlertSink["kind"];
export const ALERT_KINDS: AlertKind[] = ["ntfy", "webhook", "telegram", "discord", "slack", "email"];
export const ALL_EVENTS = ["run_start", "run_end", "safety", "reconnect", "warning", "error"] as const;

export function kindLabel(kind: AlertKind): string;
/** A fresh draft for `kind` with sensible defaults (events preselected, port 587…). */
export function defaultDraft(kind: AlertKind, id: string): AlertSinkInput;
/** First validation error for a draft, or null if send-able. `tokenConfigured`
 *  = the secret is already stored server-side (so a blank secret is OK on edit). */
export function validateDraft(d: AlertSinkInput, tokenConfigured: boolean): string | null;

export interface HealthVerdict { tone: "good" | "warn" | "bad" | "dim"; label: string; detail: string; }
export function deriveSinkHealth(sink: AlertSink, health: AlertHealth | null): HealthVerdict;
export function deadmanVerdict(health: AlertHealth | null): HealthVerdict;
```

**Steps**

1. **Implement `validateDraft`** (the meaty per-kind logic):

   ```typescript
   const isHttp = (u: string) => /^https?:\/\/.+/i.test(u.trim());

   export function validateDraft(d: AlertSinkInput, tokenConfigured: boolean): string | null {
     const secret = (d.token ?? "").trim();
     const haveSecret = secret !== "" || tokenConfigured;
     switch (d.kind) {
       case "ntfy":
       case "webhook":
         if (!isHttp(d.url)) return "Enter an http(s) URL";
         return null;
       case "telegram":
         if (!(d.chat_id ?? "").trim()) return "Telegram needs a chat id";
         if (!haveSecret) return "Telegram needs a bot token";
         return null;
       case "discord":
       case "slack":
         if (secret !== "" && !isHttp(secret)) return "Webhook must be an http(s) URL";
         if (!haveSecret) return `Paste the ${kindLabel(d.kind)} webhook URL`;
         return null;
       case "email": {
         if (!(d.smtp_host ?? "").trim()) return "SMTP host is required";
         const port = d.smtp_port ?? 0;
         if (!(port >= 1 && port <= 65535)) return "SMTP port must be 1–65535";
         if (!(d.smtp_from ?? "").trim()) return "From address is required";
         if (!(d.smtp_to ?? "").trim()) return "At least one recipient is required";
         return null;   // auth optional (open relays exist)
       }
     }
   }
   ```

2. **Implement `deriveSinkHealth`** (queue beats verified beats untested):

   ```typescript
   export function deriveSinkHealth(sink: AlertSink, health: AlertHealth | null): HealthVerdict {
     if (!sink.enabled) return { tone: "dim", label: "Disabled", detail: "Not receiving alerts" };
     const queued = health?.undelivered_by_sink[sink.id] ?? 0;
     if (queued > 0) return { tone: "bad", label: `${queued} queued`, detail: "Delivery is failing — retrying" };
     if (sink.verified) return { tone: "good", label: "Verified", detail: "Last test delivered" };
     return { tone: "warn", label: "Untested", detail: "Send a test to verify delivery" };
   }

   export function deadmanVerdict(health: AlertHealth | null): HealthVerdict {
     const dm = health?.deadman;
     if (!dm?.configured) return { tone: "dim", label: "Not set", detail: "No external monitor configured" };
     return dm.healthy
       ? { tone: "good", label: "Pinging", detail: "External monitor is being pinged" }
       : { tone: "bad", label: "Unreachable", detail: "Monitor URL is not being reached — check it" };
   }
   ```

3. **Test** `ui/src/lib/__tests__/alertSinks.test.ts` — inline-assert harness
   copied from `eta.test.ts:22-40`:

   ```typescript
   import { validateDraft, deriveSinkHealth, deadmanVerdict, defaultDraft } from "../alertSinks";
   import type { AlertSink, AlertHealth } from "../../types";
   // ... same test()/eq()/assert() harness as eta.test.ts ...

   test("ntfy requires an http url", () => {
     eq(validateDraft(defaultDraft("ntfy", "a"), false) !== null, true);
     eq(validateDraft({ ...defaultDraft("ntfy", "a"), url: "https://ntfy.sh/t" }, false), null);
   });
   test("discord accepts a stored secret with a blank input on edit", () => {
     const d = defaultDraft("discord", "d");
     eq(validateDraft(d, false) !== null, true);                 // nothing set
     eq(validateDraft(d, true), null);                           // secret already stored
     eq(validateDraft({ ...d, token: "https://discord.com/api/webhooks/1/x" }, false), null);
     eq(validateDraft({ ...d, token: "not-a-url" }, false) !== null, true);
   });
   test("email requires host/from/to and a valid port", () => {
     const e = { ...defaultDraft("email", "e"), smtp_host: "smtp.x", smtp_from: "a@x", smtp_to: "b@x" };
     eq(validateDraft(e, false), null);
     eq(validateDraft({ ...e, smtp_port: 0 }, false) !== null, true);
     eq(validateDraft({ ...e, smtp_to: "" }, false) !== null, true);
   });
   test("health verdict: queue > verified > untested; disabled dims", () => {
     const base: AlertSink = { id: "n", kind: "ntfy", enabled: true, url: "", min_level: "warning",
                               events: [], verified: true, heartbeat_min: 0 };
     const h: AlertHealth = { undelivered: 2, undelivered_by_sink: { n: 2 },
                              deadman: { configured: false, healthy: false, last_ping_age_s: null } };
     eq(deriveSinkHealth(base, h).tone, "bad");                  // queued wins over verified
     eq(deriveSinkHealth(base, null).tone, "good");              // verified
     eq(deriveSinkHealth({ ...base, verified: false }, null).tone, "warn");
     eq(deriveSinkHealth({ ...base, enabled: false }, h).tone, "dim");
   });
   test("deadman verdict maps configured/healthy", () => {
     eq(deadmanVerdict(null).tone, "dim");
     eq(deadmanVerdict({ undelivered: 0, undelivered_by_sink: {},
        deadman: { configured: true, healthy: true, last_ping_age_s: 5 } }).tone, "good");
     eq(deadmanVerdict({ undelivered: 0, undelivered_by_sink: {},
        deadman: { configured: true, healthy: false, last_ping_age_s: null } }).tone, "bad");
   });
   // ... console.log summary + process.exit(1) on failure, per eta.test.ts:185-188 ...
   ```

   Commands / expected:
   ```
   cd ui && npx tsx src/lib/__tests__/alertSinks.test.ts
   # alertSinks.test: N/N passed
   cd ui && npx tsc -b
   # no errors
   ```

**Impl tier:** Sonnet — pure functions, fully specified; verified by a real
`tsx` run.

---

### Task U3 — `AlertsPanel` + Settings tab

**Files**
* Create: `ui/src/components/settings/AlertsPanel.tsx`.
* Modify: `ui/src/components/settings/SettingsView.tsx` (tab `:62-81`; body
  `:134-186`).

**Interfaces**

`AlertsPanel(): JSX.Element` — self-contained, no props (reads `useConfig()` for
the sink list, fetches `getAlertHealth()` on mount + on the `config` WS bounce).

**Behavior (mirror the cited idioms — this is render/wiring, gated by `tsc -b`):**

1. **List** existing sinks from `useConfig()?.alerts` (already redacted;
   `token_configured`/`verified` present). Each row: kind glyph + label + the
   `deriveSinkHealth` badge (tone→existing `good/warn/bad/dim` classes, as in
   `SafetyPanel.tsx:167-174`) + Test / Edit / Delete buttons.
2. **Add / edit form**: kind `<select>` → `defaultDraft`; per-kind fields
   (`url` for ntfy/webhook; `chat_id`+secret for telegram; a single webhook-URL
   secret input for discord/slack; host/port/user/from/to/starttls+password for
   email). The **secret input is write-only** — placeholder `"(unchanged)"` when
   `token_configured`, never seeded from config — copied verbatim from the
   Astrospheric key field (`WeatherPanel.tsx:155-177`).
3. **Events multiselect** from `ALL_EVENTS`; `min_level` segmented;
   `heartbeat_min` number.
4. **Save** = `upsertAlert(draft)`; on success `await useStore.getState()
   .loadConfig()` (the server also broadcasts `config`, which the store folds via
   `loadConfig` at `store.ts:1124`) and `enqueueToast({level:"success", …})`.
   Validate with `validateDraft` first; on error `enqueueToast({level:"error",
   title})`. 409 → reload + retoast (`WeatherPanel.tsx:96-98`).
5. **Test button** = `testAlert(id)`; toast `ok`/`error` and reload config so the
   verified badge refreshes.
6. **Delete** = confirm (reuse `confirmDialog`, as `SafetyPanel.tsx:113`) →
   `deleteAlert(id)` → reload.
7. **Dead-man's-switch card**: `deadmanVerdict(health)` badge + a write-only URL
   input that POSTs `{deadman_url}` via the existing config patch
   (`api/backends.ts` pattern; `deadman_url` cap-mapped at `app.py:1587`). Empty =
   unchanged (`deadman_configured` marker at `types.ts:532`).
8. **RBAC**: `const canEdit = useCan("config.alerts")`. When false, render the
   honest-disabled note (dim + lock glyph + `aria-disabled` + `title`) exactly as
   `DriversPanel.tsx:434-439`; never a native `disabled` on the primary actions.
9. **Wire the tab** in `SettingsView.tsx`: add `{ value: "alerts", label:
   "Alerts" }` to `TABS` when `useCan("config.alerts")` (fold into the `Tab`
   union at `:39-46`), and `{activeTab === "alerts" && <AlertsPanel/>}` beside the
   other bodies (`:134-147`). Import at the top with the other panels (`:36-37`).

**Steps**

1. Build `AlertsPanel.tsx` per the behavior above, binding all decisions to the
   pure `alertSinks` helpers (no logic duplicated in the component).
2. Add the tab + body + import in `SettingsView.tsx`.
3. Gate: `cd ui && npx tsc -b` ⇒ **no errors**. (No DOM test — pure logic lives
   in U2; the panel is thin render verified by the typechecker, per constraints.)

**Impl tier:** Sonnet — assembly of cited idioms (WeatherPanel draft/save,
SafetyPanel badges/confirm, DriversPanel lock note); all real logic is already
tested in U2.

---

## 4. Open decisions

1. **Discord/Slack webhook secret in `token` vs. kind-aware `url` redaction.**
   *Recommendation:* keep it in `token` (chosen above). It reuses the hardened
   redact + "empty-means-unchanged" merge + `_scrub` with zero changes to that
   code; the alternative (teach `redacted()`/merge to strip the secret path from
   `url` for discord/slack) touches three hardened call sites for no benefit.

2. **Where the SMTP recipient list is validated.** *Recommendation:* client-side
   comma-split + non-empty in `validateDraft` (done); the server splits again in
   `_smtp_send_blocking` and soft-errors on an empty result. No new server 422 —
   an all-whitespace `smtp_to` simply fails the round-trip test and never
   verifies, consistent with the rest of the sink model.

3. **Should a changed `smtp_*` field reset `verified`?** *Recommendation:* yes —
   fold the SMTP fields into both identity-change checks (Task B1 step 4). A
   re-pointed relay must be re-tested, matching the `url/token/chat_id/kind`
   contract at `app.py:1538`.

4. **Deadman URL editor placement.** *Recommendation:* put it on the Alerts panel
   (a dedicated card) rather than a separate surface — it is the same mental model
   ("how I get paged") and shares the `config.alerts` cap
   (`app.py:1587,1596-1597`). It POSTs through the existing `/api/config`
   `deadman_url` patch, not a new route.

5. **Channel glyphs.** The icon set (`ui/src/components/icons.tsx:10-21`) has no
   discord/slack/mail icons. *Recommendation:* reuse existing glyphs (`link` for
   webhook/discord/slack, `alert` for the panel header) for this slice; adding
   brand SVGs is a cosmetic follow-up, not a blocker.
