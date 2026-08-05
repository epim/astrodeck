# Audit findings, adversarially verified (2026-08-04)

Produced by a 38-agent verification pass over the broken-promises sweep. Every
entry below was CONFIRMED after an agent was told to try to refute it; the
refuted ones are not here. The workflow journal that produced this is
session-scoped and is gone, so this file is the record.

Each entry names the CLAIM and the code that fails it. That pairing is the point:
this is not a list of bugs, it is a list of places where the software says one
thing and does another. See `docs/superpowers/specs/2026-08-04-broken-promises-audit-plan.md`.

**Status legend:** entries marked DONE were fixed and shipped in 0.2.42/0.2.43.
Everything else is open.

**DONE (shipped 0.2.42/0.2.43).**

## 1. [blocker] The `require_guiding` / `guiding_action="abort"` gate lives entirely INSIDE `if self.plan.guide and self.hub.guider and self.hub.guider.connected:`, so it only fires when a guider exists AND is connec

**Files:** server/astrodeck/sequence/engine.py, server/astrodeck/hub.py, server/astrodeck/devices/orchestrator.py, server/astrodeck/devices/backends/native_backend.py, server/astrodeck/guide/native.py, server/astrodeck/api/app.py, ui/src/components/settings/EscalationPanel.tsx, server/tests/test_engine_timeouts_escalation.py

**Fix:**

1. server/astrodeck/sequence/engine.py:1150 -- lift the escalation OUT of the guider-exists test. Restructure `_setup_target` so the decision is made whenever the PLAN asked for guiding:

```python
if self.plan.guide:
    cfg = self._cfg
    require_guiding = bool(cfg and cfg.escalation.require_guiding)
    action = (cfg.escalation.guiding_action if cfg else "warn")
    g = self.hub.guider
    if g is None or not g.connected:
        why = ("no guider is connected" if g is None
               else f"the guider ({getattr(g, 'name', 'guider')}) is not connected")
        if require_guiding and action == "abort":
            bus.log("error", f"guiding required but {why}", "sequence")
            raise SafetyAbort(f"guiding required but {why}")
        if require_guiding and action == "skip":
            bus.log("warning", f"guiding required but {why} -- skipping {target.name}",
                    "sequence")
            raise StopTarget("guiding required but no guider is connected")
        bus.log("warning", f"this plan asks for guiding but {why} -- "
                           f"continuing UNGUIDED", "sequence")
    else:
        self._set_state(detail="starting guiding")
        # ... existing lines 1160-1175 verbatim (try/_bounded/except ladder) ...
```
Anchor the new branch on `self.plan.guide` alone -- that is the line that separates the two cases the report asked to distinguish. `plan.guide == False` (models.py:169, an unguided run BY CHOICE) must keep reaching none of this and must never abort. `plan.guide == True` + require_guiding is the armed promise and must escalate whether the guider threw, was disconnected, or was never there.

2. Do NOT change the existing `except Exception` ladder at 1163-1175 -- the present-and-connected path already behaves correctly an

---

**DONE (shipped 0.2.42/0.2.43).**

## 2. [blocker] Site latitude/longitude are recoverable by a VIEWER (view.status + view.preview only) -- confirmed by live recovery to 2.9 km from three authorized requests -- because the view.site_precise seam is a 

**Files:** server/astrodeck/api/app.py, server/astrodeck/api/redact.py, server/astrodeck/hub.py, server/astrodeck/catalog/visibility.py, server/astrodeck/catalog/framing.py, server/astrodeck/sequence/report.py, server/astrodeck/auth/capabilities.py, server/tests/test_rbac_enforcement.py, docs/guide/site-and-locations.md, docs/guide/remote-access-and-roles.md

**Fix:**

The seam's premise is wrong and must be restated before any line changes: view.site_precise cannot be enforced by removing four key names, because every route above returns f(lat, lon). Fix the premise (redact.py:130-133 contract) to "no value computed from the site may reach a non-holder", then close each surface.

There is a real product call embedded here -- a viewer's Monitor shows "Alt 46 deg", and removing it changes what a viewer link is for. Quantizing is NOT a fix: averaging many quantized samples recovers the underlying value, which is exactly what the 2.9 km measurement demonstrates. So pick one of two coherent outcomes and do not ship a third:
  (A) the capability means what docs/guide/site-and-locations.md:113-125 says -- then the fields below go for non-holders; or
  (B) viewers are accepted as able to localize the rig -- then say so in both guides and stop describing view.site_precise as closing every surface.

Assuming (A), per surface:

1. server/astrodeck/hub.py:4347-4350 + server/astrodeck/api/redact.py. Have `_redact_site_for` (redact.py:81-106) and `_redact_ws_event` (redact.py:109-186) also pop `mount.alt` and `mount.az` for a principal lacking CAP_VIEW_SITE_PRECISE -- `_redact_ws_event` must copy `data["mount"]` before popping, exactly as it already copies `data["site"]` (redact.py:149-152), because Event.data is shared across subscribers.
2. server/astrodeck/api/app.py:1977 (`_preflight_alt`). Return `alt`/`az` only to a holder; a non-holder gets `verdict`/`horizon_min_deg`/`site_is_default`. Pass the principal in from app.py:4288 (GET /api/sequence/preflight). Note the residual: `verdict` is still a 3-level quantized channel a caller can binary-search on dec -- acceptable only if you accept a ~degree-scale leak; gating the whole route on view.si

---

**DONE (shipped 0.2.42/0.2.43).**

## 3. [blocker] `at_time` resolves "HH:MM" against TODAY'S calendar date with no roll-forward, so any post-midnight time (e.g. "03:00") is already in the past when an evening run starts and the rule fires on the very

**Files:** server/astrodeck/sequence/instructions.py, server/astrodeck/sequence/engine.py, server/astrodeck/sequence/models.py, server/astrodeck/sequence/schedule.py, ui/src/lib/instructions.ts, ui/src/lib/instructionSim.ts, server/tests/test_instructions_eval.py

**Fix:**

1. server/astrodeck/sequence/instructions.py:80-98 -- `parse_hhmm` must resolve the occurrence of HH:MM belonging to the CURRENT night rather than the current calendar day. Two options, in preference order:

   (a) Preferred (matches the copy "The clock reaches X"): add `run_start_ts: float | None = None` to `TriggerContext` (instructions.py:52-59), populate it at both engine call sites (engine.py:913 and engine.py:1362) from the value the engine already freezes for the schedule window, and resolve `at_time` to the FIRST occurrence of HH:MM at-or-after `run_start_ts`. This makes "At 03:00" mean 03:00 of the night in progress, and "At 23:00" in a 00:30 start correctly resolve to the coming 23:00 (never fires this night) or the caller's chosen semantics -- either way it is decidable and honest.

   (b) Minimal, keeps purity and touches one function: snap the candidate to the occurrence within ?12 h of `now_ts`, mirroring `_clock_time_near_now` at schedule.py:290-308 verbatim (`while candidate - now > 43200.0: candidate -= 86400.0` / `while now - candidate > 43200.0: candidate += 86400.0`). This alone kills the 18-h-in-the-past false fire.

   Do NOT hand-roll a third HH:MM parser -- factor `_clock_time_near_now` into a shared helper so schedule.py and instructions.py cannot drift.

2. ui/src/lib/instructionSim.ts:74-78 -- the dry run must be fixed in LOCKSTEP with the server, or a fixed server will silently disagree with the preview. Minutes-since-midnight cannot express the wrap; the snapshot needs the same anchor the server uses (add a run-start/night anchor to `SimSnapshot`, or compute the ?12 h snap in minutes).

3. server/tests/test_instructions_eval.py -- add a regression test that pins the clock: a rule `at_time="03:00"` evaluated at 21:00 local must NOT fire, and

---

**DONE (shipped 0.2.42/0.2.43).**

## 4. [blocker] The engine's module docstring promises a fail-closed safety gate "after every frame-boundary `_checkpoint`", but `_run_calibration` is the one capture loop with a `_checkpoint` and no `_safety_gate` -

**Files:** server/astrodeck/sequence/engine.py, server/astrodeck/hub.py, server/astrodeck/alerting.py, server/astrodeck/sequence/schedule.py, server/astrodeck/config.py

**Fix:**

In `server/astrodeck/sequence/engine.py`, inside `_run_calibration`'s frame loop, insert the gate immediately after `await self._checkpoint()` at line 1270 and before `await self._frame_alerts_tick()` at line 1272, mirroring `_run_step`:

    await self._safety_gate(context="frame", target=target)

Use `context="frame"` (NOT `"slew"`) -- calibration produces no mount motion, and `_enforce_mount_floor` is correctly reached only under `if context == "slew"` (engine.py:1637-1638), so passing the target stays inert while still letting `_on_unsafe` re-acquire/attribute the right target.

MUST NOT change: (1) `_safety_gate` itself -- b392b79's monitor-gate/mount-limits split is correct and this is purely a missing call site; (2) the `_confirm_unsafe` debounce, which already re-samples at the poller cadence rather than the frame cadence (engine.py:1655-1684) and so behaves correctly for multi-minute darks; (3) the `_solve_flat_exposure` metering loop's `save=False` trial captures -- bounded and short, gating each one is optional.

Separately (different promise, do not silently bundle): decide whether calibration targets should honor their frozen stop boundary. If yes, also add `self._enforce_stop_boundary(target)` at the same point -- but note `_run_calibration` has no `StopTarget` semantics wired for partially-shot calibration steps, so this needs its own test, unlike the safety gate which is a pure drop-in.

Regression test to add: a plan with a single `calibration=True` target, a connected safety monitor that flips unsafe at frame N, `on_unsafe="abort_park_warm"` -- assert the run raises `SafetyAbort` and stops before `step.count` frames. That test fails today.

---

**DONE (shipped 0.2.42/0.2.43).**

## 5. [blocker] The claim holds, and stronger than stated: in the was_parked branch the only completion signal is "position stopped changing", which a mount that never moved satisfies trivially -- and captured wire t

**Files:** server/astrodeck/devices/backends/zwo_am5.py, server/astrodeck/devices/serial_link.py, server/astrodeck/api/app.py, docs/hardware/zwo-am5-lx200-protocol.md, server/tests/test_zwo_am5.py, server/tests/test_mount_home.py

**Fix:**

Two changes, both in server/astrodeck/devices/backends/zwo_am5.py:

A. Order (find_home, :333-352): a parked AM5 refuses `:hP#` (protocol doc :104-113, :192-193), so unpark BEFORE commanding home, not only after -- e.g. `await self.unpark(); await self._park_now(); await self.unpark()`. The trailing unpark must stay (it is what keeps the mount usable, test_mount_home.py:80-95).

B. Completion signal (_park_now, :400-435): stability must not be accepted as completion until motion has been OBSERVED. Latch it: keep a `moved = False` flag, set it the first time `max(abs(pos[0]-prev[0])*15.0, abs(pos[1]-prev[1])) >= SETTLE_DEG`, and only allow the `return` at :432-434 once `moved` is True. If the deadline expires with `moved` still False, raise a DeviceError that says the mount NEVER MOVED (distinct from the current "mount never came to rest" text at :406) -- that is the honest report for a refused/ignored `:hP#`. Optionally also read the refusal directly: send the home command with an ack-class read that treats `lx200.REFUSED` as `_refused_error("home")` while tolerating the documented ack timeout (protocol doc :158-159) as "accepted"; the latch is required either way, since silence is not acceptance.

Must NOT change: `park()`'s idempotence short-circuit (:354-360) and its test (test_zwo_am5.py test_park_stays_idempotent); the tracking-off preamble and its ordering (:378-388), which is hardware-verified and asserted by test_zwo_am5.py:820-827 and test_mount_home.py:98-107; the `not was_parked` branch's `Gps` polling (:413-416); and the decision at :417-431 to return with an explicit "completion is unverified" warning when the mount will not report a position (that path must keep saying unverified, and must not be widened into a success).

Tests that currently pass on a mou

---

**DONE (shipped 0.2.42/0.2.43).**

## 6. [blocker] The below-altitude inter-target wait takes the earliest-waiter branch with a start anchor that is already in the PAST, so `_wait_until` returns without ever sleeping or running the safety gate -- the 

**Files:** server/astrodeck/sequence/engine.py, server/astrodeck/sequence/schedule.py, ui/src/components/sequence/SchedulePanel.tsx, server/astrodeck/api/app.py

**Fix:**

Fix in the engine, not by nulling `start_ts` in schedule.py -- engine.py:956 publishes `gs["start_ts"]` into the UI's `schedule` block, so the PRO-14 null-the-anchor trick would blank the waiting chip, and the else-branch never park-holds either.

1. engine.py:948-966 (earliest-waiter branch): derive an effective wait target from the ETA when the anchor is already past, then clamp the deadline so a wait is never zero-length. Concretely, after `start_ts, wtarget, gs = earliest`, compute
   `wait_ts = max(start_ts, now + float(gs.get("eta_s") or 0.0))`
   and use `wait_ts` for BOTH the teardown test at line 964 (`if wait_ts - now > WAIT_TEARDOWN_S: await self._park_hold()` -- this is what restores the "stop tracking a finished target" promise) and the wait at line 966: `await self._wait_until(max(wait_ts, time.time() + SCHEDULE_WAIT_STEP_S))`.
2. engine.py:1056 (defense in depth, so no caller can ever produce a no-op wait): restructure `_wait_until` as a do-while -- run `await self._checkpoint()` and `await self._safety_gate(context="frame")` at least once, and always `await asyncio.sleep(...)` at least a bounded minimum, before returning on an already-past deadline. This is what makes the docstring at engine.py:1046-1054 true.
3. Add a regression test that drives `_run_scheduled` with a `min_altitude_deg` target below its gate at `start_mode="now"` and asserts a concurrent task still gets scheduled (canary ticks > 0) and `_safety_gate` was called at least once within ~1 s.

Must NOT change: the else-branch tick at engine.py:970 (`SCHEDULE_WAIT_STEP_S` re-evaluation for an unresolvable start), the frozen-window semantics at engine.py:854-860, the `start_ts`/`stop_ts` values `gating_status` returns (the UI chip and `schedule.ts` read them), and the `never_rises` / `window_

---

**DONE (shipped 0.2.42/0.2.43).**

## 7. [blocker] The claim holds and is understated: `_redact_site_for` does strip the four site keys, but three sibling surfaces on the SAME `view.status` tier (`GET /api/sequence/preflight`, `mount.alt/az` in `/api/

**Files:** server/astrodeck/api/redact.py, server/astrodeck/api/app.py, server/astrodeck/hub.py, server/astrodeck/auth/capabilities.py, server/astrodeck/catalog/visibility.py

**Fix:**

Either make the invariant true or delete the promise -- do not leave both. If confidentiality is kept:

1. server/astrodeck/api/app.py:4295-4300 (`GET /api/sequence/preflight`) -- the attacker-chosen-probe oracle. Preferred: raise the gate to `CAP_CONTROL_CAPTURE`, matching its POST sibling at app.py:4302; the only callers are plan/atlas/mount surfaces (ui/src/components/PreflightStrip.tsx:62, ui/src/views/AtlasView.tsx:706, ui/src/views/MountView.tsx:90), none of which a viewer drives. Otherwise thread the principal into `_preflight_alt` (app.py:1961-1978) and, for a non-holder of `CAP_VIEW_SITE_PRECISE`, return only `verdict`/`horizon_min_deg`/`site_is_default` with `alt`/`az` ABSENT (ui/src/lib/preflight.ts:223-235 already null-checks `pf.alt`). Do NOT "fix" by coarsening alt/az -- repeated probing averages rounding noise away; absence is the only strip that holds.

2. server/astrodeck/hub.py:4348-4351 -- `mount.alt` / `mount.az`. Strip both from `payload["mount"]` for a non-holder inside `_redact_site_for` AND inside `_redact_ws_event` (redact.py:96-100 and 143-173), so both /ws lanes and REST share the one seam. UI consumers that must degrade: ui/src/App.tsx:538, ui/src/components/SlewPad.tsx:112-113 and 475-477, ui/src/components/weather/RadarMap.tsx:291-302, ui/src/lib/slewController.ts:70. Leave `ra_hours`/`dec_deg`/`ra_str`/`dec_str` alone -- sky coordinates disclose nothing without the observer.

3. server/astrodeck/hub.py:4225-4237 -- gate `meridian["hours_to_flip"]` the same way (LST-equivalent), noting it only populates on a GEM with flip enabled.

4. server/astrodeck/catalog/visibility.py:662 and :686 -- `/api/visibility` and `/api/visibility/order` return unrounded alt/sun_alt series + dark_start_unix/dark_end_unix/transit_unix from `hub.site`; same decis

---

**DONE (shipped 0.2.42/0.2.43).**

## 8. [important] `POST /api/remote/config` takes a whole `AuthConfig` and passes it straight to `set_auth` without the `_preserve_auth_secrets` guard its sibling route 20 lines above uses, so a relay-pairing save wipe

**Files:** server/astrodeck/api/app.py, server/astrodeck/config.py, docs/relay-deploy.md, ui/src/api/backends.ts

**Fix:**

Make the route accept only what its own docstring says it writes, rather than a whole AuthConfig.

1. Add a narrow request model beside the other `*Body` models at MODULE scope in server/astrodeck/api/app.py (module scope is required -- the file uses `from __future__ import annotations`, and the comment at app.py:1463-1468 records that a model defined inside `create_app()` silently degrades to a query param):

    class RemotePubkeysBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        relay_pubkey: str | None = None
        viewer_link_pubkey: str | None = None

2. Rewrite the handler body at app.py:4991-4999 to merge onto the STORED config instead of replacing it:

    async def set_remote_config(body: RemotePubkeysBody):
        auth = config_store.cfg().auth
        updates = {k: v for k, v in
                   (("relay_pubkey", body.relay_pubkey),
                    ("viewer_link_pubkey", body.viewer_link_pubkey))
                   if v is not None}
        auth = auth.model_copy(update=updates)
        try:
            cfg = await asyncio.to_thread(config_store.set_auth, auth)
        ...

   This closes the secret wipe AND the `methods`/`role_allowlist`/`default_role` wipe, which a bare `_preserve_auth_secrets` call would not. If the route must keep accepting a full `AuthConfig` for compatibility, the minimum acceptable patch is inserting `auth = _preserve_auth_secrets(auth)` at app.py:4996 (mirroring line 4976) -- but that leaves failure scenario (b), the auth-off case, wide open.

3. Add the missing coverage in server/tests/ (grep confirms zero tests touch this path): POST a redacted auth echo and assert `cfg.auth.google_client_secret` / `admin_token` / `session_private_key` are unchanged; POST `{"relay_pubkey": "X"}` and assert `cfg.aut

---

**DONE (shipped 0.2.44).**

## 9. [important] The route is `GET /api/discover/alpaca` (plus `/api/discover/nina`), both gated on `view.status` -- a VIEWER capability -- and the guard's comment claiming it "defeats DNS rebinding" is false: the val

**Files:** server/astrodeck/devices/alpaca.py, server/astrodeck/devices/nina.py, server/astrodeck/api/app.py, server/astrodeck/auth/capabilities.py, server/astrodeck/remote/relay_client.py

**Fix:**

1. Pin the connection to the address that was validated. Change `validate_scan_host` (server/astrodeck/devices/alpaca.py:146) to RETURN the approved address list instead of `None` (keep raising the same `AlpacaScanError` kinds so no caller's error handling changes), then in `query_server` (alpaca.py:213-217) build the URL from the approved IP literal rather than the name:
   - `addrs = validate_scan_host(host, port)`
   - `url = f"http://{addrs[0]}:{port}/management/v1/configureddevices"` (bracket IPv6), and pass `headers={"Host": f"{host}:{port}"}` so a vhosted target still answers. Do NOT keep `host` in the URL authority -- that is the whole defect.
   - Leave `follow_redirects` unset (default False). If it is ever turned on here, the pinning is void.
2. server/astrodeck/devices/nina.py:1069 -- delete the `socket.gethostbyname(h)` second resolution and append the address `validate_scan_host` just approved: `candidates.extend(validate_scan_host(h, port))`. Keep the `except (AlpacaScanError, OSError): pass` skip semantics (discovery must not 500 on one bad host).
3. Correct the three comments that assert an immunity the code does not have -- alpaca.py:131-134 ("Checking the resolved IP ... is what defeats DNS rebinding"), alpaca.py:154-155 ("SSRF guard that also defeats DNS rebinding"), alpaca.py:182 ("defeats DNS rebinding"). After the pinning change they become true; if the pinning is not done, they must instead say the guard blocks literals and stable-DNS names only and does NOT survive a rebinding TOCTOU.
4. Optional, same route, to close the oracle app.py:1656-1658 claims is already closed: drop `(HTTP {r.status_code})` from the `not_alpaca` message at alpaca.py:228-231, or collapse `unreachable`/`timeout`/`not_alpaca` into one message when the caller lacks `config

---

**DONE (shipped 0.2.44).**

## 10. [important] CONFIRMED -- `GET /api/profiles/{id}` is gated only on `view.status` (which a VIEWER holds) and `redact_profile` scrubs nothing but secret-looking keys inside `devices[].extra`, so a viewer/relay-link

**Files:** server/astrodeck/api/app.py, server/astrodeck/profiles.py, server/astrodeck/api/redact.py, server/astrodeck/config.py, server/astrodeck/auth/capabilities.py, server/tests/test_profiles.py, server/tests/test_rbac_enforcement.py

**Fix:**

Make the detail route principal-aware and apply the SAME holder rule the rest of the codebase uses (`principal.has(CAP_CONFIG_BACKEND)` -> untouched; everyone else -> addressing stripped).

1. server/astrodeck/api/redact.py -- add `_redact_profile_for(payload: dict, principal: Principal | None) -> dict` next to `_redact_drivers_for` (mirror it exactly: early-return for a `config.backend` holder, non-dict guard, COPY rows never mutate in place, add to `__all__`). It must remove from each `devices[]` row: `host`, `port`, `port_path`; and from the top level: `nina_host`, `nina_port`, `phd2_host`, `phd2_port`. Leave `role`, `backend`, `name`, `dev_type`, `dev_num`, `transport`, `driver_id` (availability/identity, not addressing -- matches the drivers rule that keeps `id`/`transport`/`index`).

2. server/astrodeck/api/app.py:2646-2653 -- change the signature to `async def get_profile(profile_id: str, principal: Principal = Depends(require(CAP_VIEW_STATUS)))` (the surrounding routes at 2137, 2510, 2962 already use this exact form) and return `_redact_profile_for(redact_profile(profiles.get(profile_id)), principal)`. Keep the 404 mapping for `(KeyError, FileNotFoundError)` byte-for-byte -- it is the path-traversal guard's cover story (profiles.py:308-318).

3. server/astrodeck/api/app.py:2638-2644 -- strip `site_name` from the LIST rows (and from the detail payload) for a caller lacking `CAP_VIEW_SITE_PRECISE`, consistent with `_SITE_STRIP_KEYS` in redact.py:49. Same principal plumbing.

4. Test: add to server/tests/test_rbac_enforcement.py (which has zero profile coverage today) a case using the existing `_install(principal_for_role("viewer"))` harness asserting a viewer's `GET /api/profiles/{id}` contains no `host`/`port_path`/`nina_host`/`phd2_host`, and an admin's does.

M

---

**DONE (shipped 0.2.42/0.2.43).**

## 11. [important] The pier-collision half of `check_slew_limits` is dead on the one path it was added for: the branch is gated on `self.plan`, which is None in the fresh post-reboot process where ResumeArm gates its un

**Files:** server/astrodeck/sequence/engine.py, server/astrodeck/sequence/resume_arm.py, server/tests/test_resume_after_restart.py

**Fix:**

Make the flip setting an explicit input to the gate instead of reading run-scoped state, exactly as `cfg` already is.

1. engine.py:2013 -- add a parameter to `_enforce_mount_floor`, e.g. `plan=None`, and resolve it the same way `cfg` is resolved at engine.py:2031: `plan = plan if plan is not None else self.plan`.
2. engine.py:2057-2059 -- replace `and self.plan and not self.plan.meridian_flip` with the resolved local, `and plan is not None and not plan.meridian_flip`. Keep it as a `plan is not None` test rather than a truthiness test so a plan object that happens to be falsy cannot silently disarm the guard again.
3. engine.py:2001 -- add the same `plan=None` keyword to `check_slew_limits` and forward it at engine.py:2010-2011, and extend the docstring the way the `cfg` sentence already does, naming the no-run caller.
4. resume_arm.py:333 -- pass the plan that is already in hand: `await self.engine.check_slew_limits(tgt, cfg=cfg, plan=session.plan)`. `session.plan` is already dereferenced one line up at resume_arm.py:324 and is the same object handed to `engine.start(armed.plan, session=armed)` at resume_arm.py:172, so the resume gate and the run gate then read identical settings.
5. server/tests/test_resume_after_restart.py:286 -- the stub `check_slew_limits` must grow the `plan` keyword, and a test should assert the plan is forwarded. Better: add a test that drives the REAL `SequenceEngine._enforce_mount_floor` with `self.plan is None`, `enforce_pier_limits=True`, `meridian_flip=False`, and a mount reporting EAST vs destination WEST, asserting SafetyAbort. Without that, the stub at line 286 keeps this class of defect invisible.

MUST NOT CHANGE:
- The UNKNOWN-passes semantics at engine.py:2050-2051 and the `side in (EAST, WEST) and cur in (EAST, WEST)` requirement --

---

**DONE (shipped 0.2.44).**

## 12. [important] `_enforce_stop_boundary` is wired only into the light-frame loop in `_run_step`; `_run_calibration`'s frame loop never calls it, so a calibration target's "stop at dawn" / "max run" boundary is enforc

**Files:** server/astrodeck/sequence/engine.py, server/astrodeck/sequence/schedule.py, server/astrodeck/sequence/models.py, ui/src/views/SequenceView.tsx, ui/src/components/sequence/SchedulePanel.tsx, server/tests/test_sequence_engine_fixes.py

**Fix:**

Add the missing per-frame check to `_run_calibration`, mirroring `_run_step`'s placement (1314 -> 1319): in server/astrodeck/sequence/engine.py, inside the `for i in range(...)` loop at line 1269, immediately after `await self._checkpoint()` (line 1270), insert `self._enforce_stop_boundary(target)`.

It must be INSIDE the inner per-frame loop, not the outer `for si, step in enumerate(target.steps)` loop at 1248 -- a per-step-only check still shoots an entire 200-frame step past the boundary.

CALLER: no change needed. engine.py:917-921 already catches `StopTarget` around the `_run_calibration` call at :904 and does `mark_skipped` + "skipped -- {e}", keeping the night going.

MUST NOT CHANGE / required care: raising out of the loop bypasses the `if panel_lit: await self._panel_off_safe()` at engine.py:1288-1289. `StopTarget` is CAUGHT by the scheduler and the night continues, so this is not the "abort/teardown" path the comment at 1252-1253 relies on (`_panel_off_safe` is otherwise only called at :2810 and :2819, both wind-down). So a flat step interrupted mid-window would leave the calibration panel lit through the following targets. Wrap the per-step body in `try: ... finally: if panel_lit: await self._panel_off_safe()` (or equivalent) as part of this fix.

Do not alter `_enforce_stop_boundary` itself -- its `if not win: return` and `stop_ts is None` early-outs already make it inert for targets with no boundary (`stop_mode="none"`, `max_run_min=0`, the default per models.py:43-46), so plans that set no window are byte-identical.

TEST: add a calibration-target analogue of `test_run_step_does_not_shoot_past_closed_window` (server/tests/test_sequence_engine_fixes.py:207) asserting `_run_calibration` captures no further frames once `_frozen[id(target)]` has a past `stop_t

---

**DONE (shipped 0.2.44).**

## 13. [important] Temperature-drift refocus is gated on a baseline (`_last_focus_temp`) that ONLY an autofocus can write, so a plan with `autofocus_every = 0` and no target running an initial AF never refocuses on drif

**Files:** server/astrodeck/sequence/engine.py, server/astrodeck/sequence/models.py, ui/src/views/FocusView.tsx, ui/src/views/SequenceView.tsx, ui/src/views/AtlasView.tsx, ui/src/lib/preflight.ts, server/tests/test_sequence.py

**Fix:**

Make the baseline self-seeding so the promise no longer depends on an autofocus having run.

1. server/astrodeck/sequence/engine.py:2556 -- replace the `and self._last_focus_temp is not None` gate with a seed-on-first-use. Enter the branch whenever `plan.refocus_on_temp_delta_c > 0`, read `t` as today, and when `t is not None and self._last_focus_temp is None`, set `self._last_focus_temp = t` and `return False` (arm from the run's starting temperature -- that is what "refocus when focuser temp drifts this much" means for a user who focused manually). Keep the comparison at 2561 untouched for the already-seeded case.
   MUST NOT CHANGE: the `if "focuser" not in self.hub.devices: return False` early return at 2552-2553; the `autofocus_every` branch at 2554-2555 (it must still fire without any temperature); the `except Exception: t = None` and `if t is not None` guards -- a focuser with no temperature probe must remain a silent no-op, never a crash and never a refocus loop.

2. server/astrodeck/sequence/engine.py:2757-2765 -- a failed/raised autofocus must still record where the temperature now is. Move the `self._last_focus_temp = await foc.get_temperature()` capture so it also runs on the outer `except Exception` path (e.g. a small `_capture_focus_temp()` helper called in both arms, still individually try/except'd and still swallowing errors). Do not change `af_failure_action` handling at 2767-2775.

3. ui/src/views/FocusView.tsx:212 -- with (1) in place `refocusArmed` becomes truthful and needs no change. If (1) is rejected, then FocusView must stop claiming the temp arm when no target can seed it: gate the ?temp clause on `plan.targets.some(t => t.autofocus_first) || plan.autofocus_every > 0`, and say why when it is off.

4. server/tests/test_sequence.py:143-149 -- the

---

**DONE (shipped 0.2.44).**

## 14. [important] The per-target "if missed: wait | skip" control is inert: `Schedule.on_missed` is written to the plan, round-tripped through the API into session ledgers, and rendered in the target summary, but no se

**Files:** server/astrodeck/sequence/models.py, server/astrodeck/sequence/engine.py, server/astrodeck/sequence/schedule.py, ui/src/components/sequence/SchedulePanel.tsx, ui/src/lib/schedule.ts, docs/guide/plan-and-sequences.md, server/tests/test_config_automation.py

**Fix:**

Either wire the field or delete the control -- do not leave a toggle that writes an unread value.

WIRE IT (preferred; keeps the shipped plan schema):
1. server/astrodeck/sequence/engine.py, in the `if ready is not None:` arm at :896, before `ti = index_of[id(ready)]`: if `ready.schedule.on_missed == "skip"` and the target's frozen start is already in the past by more than a grace period -- `st = (frozen.get(id(ready)) or (None, None))[0]`, `st is not None and now - st > MISSED_GRACE_S` -- then `bus.log("info", f"{ready.name}: start window missed -- skipping", "sequence")`, `self.reporter.mark_skipped(ready)` (guarded by `if self.reporter`), `remaining.remove(ready)`, `continue`. Use the FROZEN window (engine.py:854-857), never a re-resolved one, or a past dusk rolls forward and the branch goes unreachable -- the ?1.6 bug the comment at :846-853 documents. Define `MISSED_GRACE_S` next to `WAIT_TEARDOWN_S`/`SCHEDULE_WAIT_STEP_S`; it must be non-zero so a target that becomes ready at its own start instant is not skipped by float jitter.
2. server/astrodeck/sequence/models.py:47: tighten to `on_missed: Literal["wait", "skip"] = "wait"` (`Literal` is already imported at :5) so a malformed value 422s at the plan route instead of silently reading as "wait".
3. server/astrodeck/sequence/engine.py:937-945 (`all_closed`) and :889-890: leave the window_closed/never_rises handling exactly as is under BOTH settings -- a closed window cannot be waited for, and changing it would resurrect the ~23h stall. Instead fix the copy in ui/src/components/sequence/SchedulePanel.tsx:222 so "wait" no longer promises the engine holds a target whose window has already closed; state the actual rule ("wait -- run it whenever its window is open, even if the start was missed; a window that has closed

---

**DONE (shipped 0.2.44).**

## 15. [important] The ASIAIR focuser's `move_to` still returns SUCCESS on absence-of-motion (two idle polls = 1.0 s) and never compares the reached position to the target -- the exact defect removed from the sibling ZW

**Files:** server/astrodeck/devices/backends/asiair_backend.py, server/astrodeck/devices/backends/zwo_usb.py, server/astrodeck/focus/autofocus.py, server/astrodeck/focus/native.py, server/tests/test_asiair_backend.py, ui/src/lib/focusMove.ts

**Fix:**

In server/astrodeck/devices/backends/asiair_backend.py, replace the wait loop in `AsiairFocuser.move_to` (lines 712-725) with the arrival test already proven at zwo_usb.py:303-357, keeping everything around it:

1. Before the move RPC (:710), read and keep the start position (`start = await self.get_position()`), so a failure can say where it began.
2. In the loop, read `info` ONCE per poll and use both fields off it: `pos = int(getattr(info, "position", 0) or 0)` and the existing `state`-derived `moving`.
3. Return ONLY on arrival: `if abs(pos - position) <= 2: return` (use a module-level `ARRIVAL_TOLERANCE_STEPS = 2`, matching zwo_usb.py:28 and ui/src/lib/focusMove.ts:35 -- the UI already hardcodes that same tolerance).
4. Track progress: `if pos != last: last, idle_polls = pos, 0; continue`. Only when the position is NOT changing AND `state` reads idle for two consecutive polls, raise a stated-reason DeviceError naming both numbers, e.g. `f"{self.name}: move to {position} did not happen -- the focuser stopped at {pos} and is no longer moving"` (mirrors zwo_usb.py:352-357). Two polls of idle is still required, so a slow motor start cannot cry wolf.
5. Add the reached position to the existing timeout message at :723-725 ("...did not settle within 180s -- halted, stopped at {pos} (started from {start})").

MUST NOT CHANGE:
- The `except BaseException: ... await self.halt() ... raise` wrapper at :726-731 -- halt-on-any-abnormal-exit is correct and must survive the rewrite.
- `halt()` at :733-736 -- never gated on idleness.
- `is_moving()` at :738-743 -- it is the one source of truth shared with the status poll and ui/src/lib/focusMove.ts; the new loop should use the same `state != "idle"` test, not a second one.
- The pre-flight range guard at :705-708 and `require_idle`

---

**DELIBERATE, NOT FIXED.** `park()` keeps its idempotence guard: re-sending
`:hP#` to a mount that reports parked costs a 60 s poll on the dawn path, which
is the one path standing between the sun and the optics. `find_home()` is the
answer to a sagged-but-parked mount and now bypasses the guard (0.2.42). A mount
that reports parked while physically 50 deg out is a hardware lie this driver
cannot detect; re-parking on every call would not detect it either.

## 16. [important] `ZwoAm5Telescope.park()` short-circuits on the mount's own `Gps` parked flag, so in the one state this driver documents as real -- an AM5 that powers up reporting PARKED with the tube gravity-sagged 5

**Files:** server/astrodeck/devices/backends/zwo_am5.py, server/astrodeck/sequence/roof.py, server/astrodeck/sequence/engine.py, server/astrodeck/api/app.py, server/astrodeck/devices/fingerprint.py, server/astrodeck/devices/alpaca.py, server/tests/test_zwo_am5.py

**Fix:**

Make the idempotence key on what THIS driver did in THIS connection, not on what the mount claims. The mount's flag survives a power cut; a session flag cannot.

In server/astrodeck/devices/backends/zwo_am5.py:
- `__init__` (near line 112): add `self._parked_by_us = False`.
- `connect()` (line 258): set `self._parked_by_us = False` after a successful open -- a fresh connection has commanded nothing, so the first park of a session always goes to the wire. This is the line that closes the power-cut case.
- `park()` (lines 354-360): replace `if await self.is_parked(): return` with `if self._parked_by_us and await self.is_parked(): return`, then `await self._park_now()`.
- `_park_now()` (line 362): set `self._parked_by_us = True` on the return paths that actually completed; leave it False on the "would not report its position" early return at line 431, since that path explicitly says completion is unverified.
- `unpark()` (line 325): clear `self._parked_by_us = False`.

Cost of the change on the dawn path: one extra `_park_now` per session on an already-home mount, which the `was_parked` branch resolves in about two PARK_POLL_S ticks (~2 s), not 60 s.

MUST NOT CHANGE: `find_home`'s unconditional `_park_now()` (line 351) and its ordering before `unpark()`; the stop-tracking-first ordering at lines 378-388 (the 2026-07-30 hardware finding -- :hP# is silently ignored while tracking); the `was_parked` position-settle completion logic at 400-435; `roof.close_observatory`'s fail-safe treatment of a failed query as NOT parked (roof.py:69-70).

server/tests/test_zwo_am5.py:862-871 `test_park_stays_idempotent` must be rewritten, not deleted: it should assert that the FIRST park after connect sends :hP# even on a mount reporting parked, and that an immediately repeated park does not

---

**DONE (shipped 0.2.44).**

## 17. [important] `_wait_stopped` promises that "two consecutive stopped samples" make it impossible to report "a settled slew that never started", but two samples taken before the mount begins moving are indistinguish

**Files:** server/astrodeck/devices/backends/asiair_backend.py, server/tests/test_asiair_backend.py, server/astrodeck/hub.py, server/astrodeck/polar/native.py, server/astrodeck/sequence/engine.py

**Fix:**

Make "settled" mean "settled AT THE TARGET", and stop reading a missing field as a measurement.

1. asiair_backend.py:578 -- give `_wait_stopped` the commanded destination: `async def _wait_stopped(self, timeout_s, what, target: tuple[float, float] | None = None)`, and at line 605 refuse to return while the mount is stationary but not near the target, e.g. `if stopped >= 2 and (target is None or _sky_delta_deg(pos, target) <= ARRIVE_EPS_DEG): return`. A stationary mount that never left the origin then keeps polling until the honest timeout at 607-610 fires ("slew did not settle") instead of returning success. Pass `(ra_hours, dec_deg)` from line 488. ARRIVE_EPS_DEG must be loose (a degree or so) -- this is an arrival check, not a centring check, and centring stays the caller's job per the docstring at 474-475. `park()` (523-539) and the focuser wait (709-724) keep their own logic; do not fold them in.

2. asiair_backend.py:628 -- distinguish absent from "none". `raw.get("move_status", "none")` silently manufactures a "stopped" reading out of a field that is not there. Return a tri-state (or have `_wait_stopped` check `"move_status" in raw` separately) so that when the field is absent the wait knows it has only one signal and requires more evidence -- e.g. a longer stability run and the target check above -- rather than counting the missing field as a stopped sample.

3. asiair_backend.py:585-588 and 625-627 -- once (1) and (2) land, rewrite both docstrings to state what is actually guaranteed. If the arrival check is what closes the window, say so; do not keep "two consecutive stopped samples so a poll landing before the motors engage cannot report a settled slew that never started", which is a guarantee the sample count alone cannot make.

4. server/tests/test_asiair_b

---

**DONE (shipped 0.2.44).**

## 18. [important] Confirmed in its core: `NinaCamera.expose` sends `set-binning` only when `binning > 1`, so a subsequent bin-1 request leaves NINA at the last raised bin while the frame is stamped `binning=1` -- reach

**Files:** server/astrodeck/devices/nina.py, server/astrodeck/hub.py, server/astrodeck/sequence/engine.py, server/astrodeck/sequence/models.py, server/astrodeck/polar/native.py, server/astrodeck/imaging/fitsio.py, server/astrodeck/devices/alpaca.py, server/astrodeck/devices/backends/asiair_backend.py

**Fix:**

server/astrodeck/devices/nina.py:271-274 -- make the setter unconditional, mirroring alpaca.py:391-394 and asiair_backend.py:1033-1034. Replace the `if binning and binning > 1:` guard with a normalised, always-sent call:

    b = max(1, int(binning or 1))
    try:
        await self.client.get("/equipment/camera/set-binning", binning=f"{b}x{b}")
    except DeviceError:
        pass

and stamp `binning=b` at nina.py:304 instead of the raw argument.

STRICTLY-HONEST OPTION (recommended, and cheap here): the swallowed DeviceError still leaves the code stamping a bin it did not confirm. NINA already reports the CURRENT bin and this file's own comment says so -- nina.py:245-247: "BinX/BinningX are the CURRENT bin (default 1)". So after the capture, read it back and stamp the observed value: `actual = int(pick(await self.info(force=True), "BinX", "BinningX", default=b) or b)` and use `binning=actual` in the CameraFrame. That closes the guard-swallow hole on an older NINA that 404s the endpoint.

MUST NOT CHANGE:
- Keep the `except DeviceError: pass` bridge tolerance -- a NINA version without `/equipment/camera/set-binning` must not fail the whole exposure; only the stamped value should become honest.
- Do NOT touch `max_bin` at nina.py:248 (that is the b0adee9 fix and is correct) and do NOT start passing BinX into it.
- Do NOT change hub.py:2311's `frame.rendered_bytes is None` gate -- the NINA-saves-its-own-file split is deliberate (hub.py:2371-2374) and unrelated.
- Do NOT "fix" XPIXSZ at imaging/fitsio.py:106 as part of this; the NINA resize error there (nina.py:292-294 fetches a 1400px render of an unbinned-sized sensor) is a separate, larger defect on the same path and needs its own change.

TEST: extend server/tests/test_nina_optics_bin.py with a fake client recording e

---

**DONE (shipped 0.2.44).**

## 19. [important] Pause on a native TPPA run is deterministically overwritten: the driver publishes `state:"running"` after `pause()` wrote `state:"paused"`, so the UI keeps showing Pause instead of Resume, and in the 

**Files:** server/astrodeck/polar/native.py, server/astrodeck/polar/session.py, ui/src/views/PolarView.tsx, ui/src/store.ts, server/astrodeck/api/app.py

**Fix:**

Make "paused" a state the driver cannot clobber, and honor it before motion and before a new exposure.

1) server/astrodeck/polar/session.py:46-51 -- in `_publish`, refuse to downgrade an explicit pause: when `self._native_paused` is True and the incoming `kw.get("state") == "running"`, drop the `state` key (keep every other field, so progress/error/direction updates still stream). Do NOT suppress terminal states -- `"done"` and `"error"` must still land, or a session that finishes while paused sticks forever. Equivalently, publish `paused=self._native_paused` as a first-class field and have the UI key off it; if you do that, ui/src/views/PolarView.tsx:238 must switch to that field and server/astrodeck/api/app.py:4292 + 4495 must include it, or a reload still shows the wrong button.

2) server/astrodeck/polar/native.py:191 -- move `await _wait_if_paused(session)` to AFTER `await asyncio.sleep(_ADJUST_INTERVAL_S)` (or add a second call between 191 and 192) so a Pause landing during the 1 s cadence sleep does not start a fresh exposure.

3) server/astrodeck/polar/native.py:174 -- call `await _wait_if_paused(session)` immediately before `await _rotate_in_ra(...)`, so Pause blocks the 12 deg slew rather than letting one through. Do NOT replace `_check_alive` there; the motion-epoch fence must keep raising `DeviceError` independently.

Do NOT change `stop()`: it cancels the task, and `_wait_if_paused`'s `await asyncio.sleep(0.1)` is the cancellation point that lets a paused session unwind (native.py:287-288 docstring). Do NOT touch the NINA/sim drivers -- `_native_paused` is documented at session.py:31-34 as native-only and they pause via the websocket at session.py:119-123.

Add a regression test (none exists today): drive `run_native` with a stub solver, call `await sessio

---

**DONE (shipped 0.2.44).**

## 20. [important] PHD2Guider hardcodes `is_arcsec=True` while converting PHD2's pixel errors with a made-up 2.0 "/px constant that no product path ever sets, so the UI unit label, the narration verdict, and the sequenc

**Files:** server/astrodeck/guide/phd2.py, server/astrodeck/guide/base.py, server/astrodeck/devices/backends/phd2_backend.py, server/astrodeck/hub.py, server/astrodeck/sequence/engine.py, ui/src/views/GuideView.tsx, ui/src/lib/guideNarration.ts

**Fix:**

Make PHD2 fail closed like its two siblings, and ask PHD2 for the scale it already knows.

1. server/astrodeck/guide/phd2.py:63-66 -- change the default to "unknown" rather than a number:
   `pixel_scale_arcsec: float | None = None`; store `self.pixel_scale = float(pixel_scale_arcsec) if pixel_scale_arcsec else 1.0` and add `self._scale_known = bool(pixel_scale_arcsec and pixel_scale_arcsec > 0)`. An explicitly-supplied scale (the conn.extra path) counts as KNOWN.

2. server/astrodeck/guide/phd2.py -- add a `_refresh_pixel_scale()` mirroring the existing best-effort `_refresh_app_state()` (phd2.py:101-113): `scale = await self._rpc("get_pixel_scale", timeout=5)`; if it is a positive number and `not self._scale_known` (never override a user-supplied value), set `self.pixel_scale = float(scale)` and `self._scale_known = True`. Call it from `connect()` right after line 98 and from the reconnect path right after line 228, wrapped in try/except so it can never raise out of connect -- same contract as `_refresh_app_state`.

3. server/astrodeck/guide/phd2.py:243-244 -- multiply only when known: use `scale = self.pixel_scale if self._scale_known else 1.0`, so an unknown scale publishes raw PHD2 pixels rather than px x 2.0.

4. server/astrodeck/guide/phd2.py:377-379 -- replace the hardcoded stamp with the honest one, matching guide/native.py:1097 and devices/nina.py:889-890:
       is_arcsec=self._scale_known,
       image_scale=round(self.pixel_scale, 3) if self._scale_known else 0.0,
   and update the comment so it states the condition rather than asserting "always arcsec".

MUST NOT CHANGE:
- devices/backends/phd2_backend.py:173-175 -- the `conn.extra['pixel_scale_arcsec']` override must keep working and must keep marking the scale KNOWN.
- guide/base.py:46-80 `rms_total_arcs

---

**DONE (shipped 0.2.44).**

## 21. [important] The native guider's idle on-demand grab is gated on the frame cache being EMPTY, and nothing ever empties it, so once any guide frame exists the "live" Guide cam preview (polled every 2.5 s on Capture

**Files:** server/astrodeck/guide/native.py, server/astrodeck/hub.py, ui/src/components/GuideFramePreview.tsx, ui/src/views/PolarView.tsx, server/astrodeck/devices/backends/sim_backend.py, server/astrodeck/api/app.py

**Fix:**

In server/astrodeck/guide/native.py, give the cache an age and re-expose when it is stale while idle.

1. Add a companion timestamp next to native.py:211 (e.g. `self._last_frame_at = 0.0`) and stamp it at BOTH write sites -- native.py:708 (`self._last_frame = frame.data`) and native.py:1404 -- using `time.monotonic()`.
2. Change the condition at native.py:1397 from `if data is None and ...` to also fire on age, e.g.
   `stale = data is None or (time.monotonic() - self._last_frame_at) > _IDLE_PREVIEW_TTL_S`
   `if stale and self.connected and not loop_running:`
   with the TTL below the panel's 2.5 s poll (GuideFramePreview.tsx:24) -- around 2.0 s -- so a poll gets a genuinely new frame rather than a re-encode. On a suppressed exposure failure, keep serving the last frame (current behavior) rather than returning None.
3. Add single-flight around the on-demand exposure in this branch -- an asyncio.Lock or a shared in-flight task on the guider -- because two overlapping pollers would now both reach `self.cam.expose(...)` on the same sensor. The hub's existing single-flight (hub.py:1291-1303, `asyncio.shield(task)`) guards ONLY the `guide_camera` device branch and does not cover this one.

Must NOT change:
- The `not loop_running` guard at native.py:1396-1397 -- while `_loop_task` owns the sensor the last looped exposure IS the truth and a second expose() would fight it (hub.py:1153-1156 states the same ordering rule).
- The never-raises contract (native.py:1392-1393, 1420-1421): every new failure path must still return None / the previous frame so the route 404s rather than 500s.
- The crop/stretch pipeline at native.py:1411-1419 and the `contextlib.suppress(Exception)` around the camera call.
- Do not "fix" this by clearing `_last_frame` in stop_guiding: native.py:903-905

---

**DONE (shipped 0.2.44).**

## 22. [important] The power-cut record is destroyed by the boot it is meant to survive: the 2 s status poller writes the post-restart focuser position over the file within ~2 s of connect, and the only reader (resume_a

**Files:** server/astrodeck/devices/fingerprint.py, server/astrodeck/hub.py, server/astrodeck/sequence/resume_arm.py, server/tests/test_resume_after_restart.py

**Fix:**

Give the fingerprint the read-before-write shape zwo_usb.py:244-249 already uses, so the value the ladder compares against predates the boot.

In server/astrodeck/devices/fingerprint.py:
1. Add a process-lifetime boot snapshot, e.g. `_boot: dict | None = None` and `_boot_loaded: bool = False` beside `_last_write` (line 32). In `record()` (line 74-78), BEFORE the first `write_json_atomic` of the process, load the existing file once into `_boot` (`_boot = read_json_or(_path(), None); _boot_loaded = True`). This must happen inside record() rather than at import, because `_path()` is deliberately resolved late (see the docstring at fingerprint.py:40-51) -- do not reintroduce a cached `_PATH`.
2. Make `verdict()` (line 92) compare against the boot snapshot when one was captured: `known = (_boot or {}).get("focuser_position")` if `_boot_loaded` else the current file's value (the un-restarted case where nothing has recorded yet). Keep the exact-integer comparison at lines 105-107 and keep the absent/unreadable -> `Verdict(focus_trusted=False)` fail-closed branch at lines 99-101 verbatim.
3. Handle the in-process device-reset case the docstring promises (lines 9-11): do not let a disconnected focuser blank the remembered number -- in record(), when `focuser_position is None`, carry the previously recorded `focuser_position` forward instead of writing None, and treat a reconnect whose position differs from the carried value as untrusted. (Equivalently: track the last position observed while the focuser was connected.)
4. Add `reset_for_tests()` (line 113) coverage for the new globals so tests can simulate a restart.

Tests to add in server/tests/test_resume_after_restart.py: one that runs the REAL recorder path (hub.poll_status, as test_poll_status_records_the_fingerprint at :18

---

**DONE (shipped 0.2.44).**

## 23. [important] CONFIRMED but materially narrowed: `flags` is published on every native TPPA update and rendered by nothing, so `_publish_error`'s docstring claim ("the additive native fields the wizard consumes") is

**Files:** server/astrodeck/polar/native.py, ui/src/views/PolarView.tsx, ui/src/store.ts, ui/src/components/polar.tsx, native/crates/astro-tppa/src/error_det.rs, native/crates/astro-tppa/src/lib.rs, native/crates/astrodeck-native/src/lib.rs, docs/native-parity/algorithms/tppa-polar-alignment.md, server/astrodeck/solve/astap.py

**Fix:**

Two changes, both small.

1. server/astrodeck/polar/native.py:316-324 -- stop shipping the redundant/dead payload and start shipping the one number that matters. Forward `position_angle_spread_deg=err.get("position_angle_spread_deg")` alongside `flags` (the PyO3 layer already exports it, astrodeck-native/src/lib.rs:725-728). Also log the spread once when it exceeds 5 deg, next to the existing solve log at native.py:183-185, so a headless/REST operator sees it at all. Correct the docstring at 314-315: it claims every additive field is consumed by the wizard, which is false.

2. ui/src/views/PolarView.tsx -- render the caveat. `polar.flags?.includes("position_angle_spread_large")` should produce a warn banner in the "Total error" panel (near PolarView.tsx:167-190), styled like the existing sim-provider warning at PolarView.tsx:226-231 so it reads at night: the three measurement frames were not a pure RA move (camera/pier angle changed by N deg), so this fit is not trustworthy -- re-run without a meridian crossing. It must sit ABOVE the knob-direction rows (PolarView.tsx:194-211), because those arrows are the thing the user would otherwise act on.

MUST NOT CHANGE:
- The polar event keys already consumed: state/source/phase/point_index/progress/message/az_error/alt_error/total_error/az_direction/alt_direction. PolarView keys the whole wizard off `phase` and `point_index` (PolarView.tsx:39-50).
- `MIN_POLE_DISTANCE_DEG` and `_refuse_near_pole` (native.py:77, 336-362) -- that guard is correct and independent; this finding is the residue it does not cover.
- Do not turn the flag into a refusal. docs/native-parity/algorithms/tppa-polar-alignment.md:700 is explicit: "None of these aborts the procedure; they are warnings only."
- Do not add `degenerate_geometry` handling in the

---

**DONE (shipped 0.2.44).**

## 24. [important] The native guider's whole calibration/PPEC persistence layer is gated on `self.profile_id`, and the only production site that builds a guider for a REAL rig hardcodes `profile_id=None` -- so on any Al

**Files:** server/astrodeck/devices/backends/native_backend.py, server/astrodeck/guide/native.py, server/astrodeck/api/app.py, ui/src/views/GuideView.tsx, server/astrodeck/devices/backends/sim_backend.py

**Fix:**

PRIMARY -- server/astrodeck/devices/backends/native_backend.py:172: replace `profile_id=None)` with the active profile's id, e.g. read it once before the optics `try` (`from ...profiles import active_profile` is already imported at line 157) and pass `profile_id=getattr(active_profile(), "id", None)`. Keep it None-safe: with no active profile the value stays None and behaviour is byte-identical to today (guiding still works, just without persistence) -- so a profile-less connect must not regress. Do NOT touch sim_backend.py:133 `profile_id="sim"` (the sim's persisted files are keyed on that literal and tests assert it). Do NOT change any of the six `if not self.profile_id:` gates -- they are the correct degradation for the no-profile case.

CACHING NOTE: `native_guider()` memoizes on `self._guider` (native_backend.py:128-129), and a session lives for one connect, so the id captured at construction belongs to the profile that opened the session -- but confirm a profile switch really closes sessions (`NativeSession.close()`), otherwise the id must be read lazily at `start_guiding` time instead.

PATH-SAFETY RIDER: once real ids start flowing, `guide/native.py:1168, :1190, :1214, :1252, :1273` interpolate `self.profile_id` straight into `CONFIG_DIR/guider/{id}.json`. Profile ids are uuid4-derived (profiles.py:76) so this is safe today; route these through the same `safe_id_path` helper `profiles.py:318` uses to keep it that way.

SECONDARY (honesty) -- ui/src/views/GuideView.tsx:650-656: inspect the response instead of toasting unconditionally, e.g. `const r = await api.del("/api/guide/calibration"); onToast("info", r?.cleared ? "Cleared saved calibration" : "There was no saved calibration to clear");`. Leave the button's `clearCalReason` gating (GuideView.tsx:520-523) alo

---

**DONE (shipped 0.2.44).**

## 25. [important] The claim holds: `SAFETY_PRESETS` is dead code on the server and `SafetyConfig.preset` is stored but never applied by anything, so the documented "safety behaviour is configured through named presets 

**Files:** server/astrodeck/config.py, server/astrodeck/api/app.py, server/astrodeck/sequence/engine.py, server/tests/test_config_dome_flags.py, docs/guide/safety-and-automation.md, ui/src/components/settings/SafetyLimitsPanel.tsx

**Fix:**

Make the label and the numerics agree on the server; the UI must stop being the only place the preset means anything.

1. server/astrodeck/config.py:954-957 -- in `set_safety`, expand the preset at the write boundary: when `safety.preset != "custom"` and `safety.preset in SAFETY_PRESETS`, apply `safety.model_copy(update=SAFETY_PRESETS[safety.preset])` before assigning to `cfg.safety`.
   CRITICAL HAZARD this must not trip: the default preset is "backyard" (config.py:109), so an unconditional patch would silently clobber the numerics of every user who hand-tuned on_unsafe/max_pause_min while leaving the label at "backyard". SAFETY_PRESETS["backyard"] (config.py:98-100) happens to equal the SafetyConfig field defaults (config.py:132-136) exactly, so it is a no-op for untouched configs but destructive for tuned ones. Either (a) only patch when the incoming preset differs from the currently stored `cfg.safety.preset`, or (b) preferred and safer: do NOT patch at all -- instead DERIVE the label on read, forcing `preset` to "custom" whenever the stored numerics match no entry in SAFETY_PRESETS. (b) can never destroy a user's values and kills the "label lies about the values" state at its root.

2. server/astrodeck/config.py:96 and :131 -- whichever of (a)/(b) is chosen, rewrite both comments to describe what the code now does. If (b) is chosen, config.py:96's "=> user-edited numerics, no patch" phrasing must go, since there is no patch in either direction.

3. ui/src/components/settings/SafetyLimitsPanel.tsx:28-48 -- once the server owns the semantics, delete the hand-mirrored `PRESETS` table (and the comment at :31-32 that documents why it exists) or serve it from the server, so the two tables cannot drift again. If the table must stay client-side for now, at minimum add `clo

---

**DONE (shipped 0.2.44).**

## 26. [important] POST /api/alerts compares the delivery identity BEFORE restoring the blanked token, so every save of a credential-bearing sink silently clears `verified` even when nothing was re-pointed -- I reproduc

**Files:** server/astrodeck/api/app.py, ui/src/components/settings/AlertsPanel.tsx, ui/src/lib/alertSinks.ts, server/astrodeck/config.py, server/tests/test_automation_api.py

**Fix:**

server/astrodeck/api/app.py, inside `upsert_alert` (the `if idx is not None:` branch, lines 2432-2443): move the token-restore ahead of the identity comparison, i.e. relocate the block currently at 2439-2442 (`# an empty token on update means "unchanged" ...` / `if not sink.token and old.token: sink = sink.model_copy(update={"token": old.token})`) to sit immediately after `old = alerts[idx]` at 2432 and before the `if (old.url != sink.url or old.token != sink.token ...)` test at 2433.

Preferred (one rule, one implementation): delete the whole duplicated block at 2432-2442 and reuse the sibling helper that already has the ordering right -- `sink = _merge_alert_verified([sink])[0]` before the `alerts[idx] = sink` / `alerts.append(sink)` split. `_merge_alert_verified` looks the old sink up from `config_store.cfg().alerts` by id and is a no-op when `old is None` (app.py:2026, 2031, 2033 all guard on `old is not None`), so it is correct for the append branch too; it must be called BEFORE `alerts[idx] = sink` since it reads the store, not the local `alerts` list. Note it currently returns sinks unchanged for a brand-new id, matching today's `else: alerts.append(sink)` behavior.

MUST NOT CHANGE:
- The reset must still fire on a genuine identity change. server/tests/test_automation_api.py:159-165 marks a tokenless ntfy sink verified, POSTs a changed url, and asserts `store.cfg().alerts[0].verified is False` -- that must stay green.
- The token restore itself must survive: a blank token from the redacted client must never blank the stored secret (`token preserved = True` in the repro).
- Outbound blanking at app.py:2448-2450 (`s.model_copy(update={"token": ""})` plus the derived `token_configured`) stays exactly as is; the client must keep receiving an empty token.
- Do NOT "f

---

**DONE (shipped 0.2.44).**

## 27. [important] rbac.py's docstring claims it "enforces the four invariants from the plan," but invariant (4) -- identity-disclosing GETs must carry an auth dependency -- is never implemented anywhere, and the invari

**Files:** server/astrodeck/auth/rbac.py, server/astrodeck/api/app.py, server/astrodeck/auth/routes.py, server/tests/test_rbac_boot_assertion.py, docs/superpowers/specs/2026-06-16-pluggable-backends-rbac-remote-design.md

**Fix:**

Two changes in server/astrodeck/auth/rbac.py; do not touch the existing exemptions in app.py:5199-5202 without a deliberate decision about `/auth`.

(a) Implement invariant (4) inside the loop at rbac.py:120-158. Give `declare()` an `identity=True` flag (stored as a third marker attr, e.g. `IDENTITY_ATTR = "_rbac_identity"`), and additionally keep an explicit `IDENTITY_PATHS = frozenset({"/api/me", "/auth/me"})` so the known ones are covered without relying on the author remembering the flag. Then, for any route where `"GET" in methods` and (`path in IDENTITY_PATHS` or the identity marker is set), raise `RouteCapabilityError` unless the route has a real `require()` dependency (see (b)) or is on an explicit, named self-gating allowlist. `/auth/me` self-gates via `resolve_principal` + 401 rather than `require()`, so it must be listed in that allowlist BY NAME with the reason -- silently inheriting the `/auth` prefix exemption is what hides it today.

(b) Stop grading the label. Split `_route_caps` (rbac.py:76-96) into `_marker_caps(route)` and `_dependency_caps(route)`, and assert `_marker_caps ? _dependency_caps ? {field-level floor}` -- i.e. a `@declare(...)` marker that is NOT backed by a matching `Depends(require(cap))` on `route.dependant` is a boot failure. Use `_dependency_caps` (not the marker) as the input to the invariant-(1) and invariant-(3) tests. This requires an explicit, documented exemption for the three field-level routes, which legitimately declare more than their route-level floor: POST /api/config, POST /api/site, PUT /api/site (they enforce the extra caps in `_require_config_field_caps`, app.py:2071). Do NOT relax the check by simply intersecting marker with deps -- that would silently re-bless case (B).

(c) Add the two missing tests to server/tests

---

**DONE (shipped 0.2.44).**

## 28. [important] The promise at guide/native.py:92-96 ("a transient guide-camera exposure fault is absorbed by bounded retry+backoff around every guide/cal exposure") is broken -- but NOT by the mechanism the candidat

**Files:** server/astrodeck/guide/native.py, server/astrodeck/devices/alpaca.py, server/astrodeck/devices/backends/native_backend.py, server/astrodeck/devices/sim.py, server/tests/test_native_guider_expose_retry.py

**Fix:**

1. server/astrodeck/guide/native.py:710 -- broaden the retried set from `except DeviceError as e:` to the transport family the adapters actually leak, e.g. `except (DeviceError, httpx.HTTPError, OSError, ValueError, struct.error) as e:` (mirroring the `(DeviceError, httpx.HTTPError, OSError)` triple used 10 times in alpaca.py), or simply `except Exception as e:` -- `asyncio.CancelledError` is a `BaseException` on this Python, so the "a stop mid-exposure is NOT retried" contract in the docstring (native.py:698-699) survives either way. Keep the exhausted-case raise at native.py:717-719 as `DeviceError`, which is what makes `_guide_loop:511` and `_calibrate` see one handled channel.

2. server/astrodeck/guide/native.py:705-708 -- add the `None` guard the hub already added for its own path (hub.py:1190-1198): after `frame = await self.cam.expose(...)`, raise `DeviceError(f"{self.cam.name} returned no frame")` when `frame is None`, so an out-of-tree driver that violates the `-> CameraFrame` annotation becomes a retryable fault instead of an `AttributeError`. Cheap, and it makes the candidate's stated mechanism unreachable by construction rather than by inventory.

3. server/astrodeck/devices/sim.py:646-648 -- the injector currently only raises `DeviceError`, which is why the suite cannot fail. Give it a settable exception type (default `DeviceError`) so a test can inject `httpx.ReadTimeout`.

4. server/tests/test_native_guider_expose_retry.py -- add one test per path that injects a NON-`DeviceError`: (a) `_expose` absorbs 2 of them and returns a frame; (b) `_guide_loop` survives 4 exhausted non-DeviceError frames (`_fault_frames == 4`, task alive) and dies on the 5th with `_lost is True`; (c) `_calibrate` raises `DeviceError`, not the raw transport error. Also update the co

---

**DONE (shipped 0.2.44).**

## 29. [minor] The phd2_backend module header asserts `discoverable = False` and "not auto-discovered", but the class sets `discoverable = True` and `discover()` really returns a PHD2 offer -- the header is stale do

**Files:** server/astrodeck/devices/backends/phd2_backend.py

**Fix:**

Documentation-only edit in server/astrodeck/devices/backends/phd2_backend.py. Change the prose to match the code; do NOT change any code.

1. Lines 12-13, replace:
```
It is not auto-discovered over the network (``discoverable = False``): the PHD2
socket is a fixed local endpoint, not a UDP-discoverable Alpaca/NINA service.
```
with text stating the real split -- the socket is a fixed local endpoint and is not UDP-discoverable like an Alpaca/NINA service, but `discoverable = True` because the BINARY is findable on this machine, so a scan offers PHD2 with `verified=False` (installed, not necessarily running) and the probe still decides reachability. Mirror the wording already in the `discover()` docstring at lines 181-192 so the two agree.

2. Line 140, replace the trailing `and not discoverable.` in the `Phd2Backend` class docstring with a phrase matching the same fact (e.g. guider-only, and discoverable only in the local-binary sense, never over the network).

Must NOT change:
- `discoverable = True` (line 146) -- the test at server/tests/test_phd2_backend.py:80 pins it and commit e9a672a set it deliberately.
- `discover()` (lines 180-197), including `verified=False`, which commit e9a672a calls "load-bearing".
- `hostless = True` and its comment (lines 147-150) -- still accurate.
- server/build/lib/... -- untracked build artifact, regenerated, not source.

---

**DONE (shipped 0.2.44).**

## 30. [minor] `build_breakdowns` is dead code that has never had a caller in any commit, and its docstring names as its use case ("re-hydrating a reporter") the exact operation the code in the same file deliberatel

**Files:** server/astrodeck/sequence/report.py, server/astrodeck/sequence/__init__.py, server/tests/test_report.py

**Fix:**

Delete `build_breakdowns` entirely -- server/astrodeck/sequence/report.py lines 236-248 (the `def` through `return totals.breakdowns()`), plus the now-orphaned blank lines up to the `# ---- reporter` banner at line 251. Nothing imports it (no `__all__` in report.py; sequence/__init__.py:2 exports only SessionReport and SessionReporter), so removal is caller-free.

If it is deliberately kept as a future utility, then at minimum strike the false example from the docstring: line 241-242's "-- e.g. when re-hydrating a reporter" must go, replaced by an explicit anti-use warning, e.g. "NOT for use on ``SessionReport.frames``: a persisted frame list may already be downsampled (see ``record_frame``/``_MAX_FRAMES``); re-hydration must go through ``_Totals.from_report``."

Must NOT change: `_Totals` (report.py:124-233) or its `add`/`breakdowns`/`from_report` methods; the live headline path `self._totals.breakdowns()` at report.py:359; the `attach_existing` seeding at report.py:427-429 including its comment; the `_MAX_FRAMES` downsample at report.py:306-308. Do not "fix" this by wiring `build_breakdowns` into `attach_existing` -- that is the bug the C1-19 comment exists to prevent.

---

**DONE (shipped 0.2.44).**

## 31. [minor] Two docstrings on the AM5 pulse-guide path describe a west strategy that the code has never used -- they name rate index R3 and a delivered rate of ~0.9x sidereal, while the code sends R2 and every ot

**Files:** server/astrodeck/devices/backends/zwo_am5.py, server/tests/test_zwo_am5.py, docs/hardware/zwo-am5-lx200-protocol.md

**Fix:**

Comment-only edit. Three lines of prose, no behaviour change.

1. server/astrodeck/devices/backends/zwo_am5.py:570-571 -- replace `west = R3+Mw\n        (~0.9x net west); n/s = R1 moves.` with `west = R2+Mw\n        (measured exactly 1x sidereal west); n/s = R1 moves.` so it matches the module note at :54-56.
2. server/astrodeck/devices/backends/zwo_am5.py:453 -- replace `(tracking-suspend east / R3-west)` with `(tracking-suspend east / R2-west)`.
3. server/tests/test_zwo_am5.py:587 -- replace `west drives R3+Mw` with `west drives R2+Mw` (the assertion on :593 is already correct and must not change).

Optionally, while in :568-572, add the missing tracking-off east fallback to the strategy list ("east with tracking already off falls back to R1+Me at 0.5x sid"), which is what test_zwo_am5.py:603 asserts.

MUST NOT CHANGE: `_PULSE_WEST_RATE_CMD = "R2"` (:60), `_PULSE_DEC_RATE_CMD = "R1"` (:59), `_PULSE_RA_RATE_DEG_S = 0.004178` / `_PULSE_DEC_RATE_DEG_S = 0.002089` (:63-64) -- all hardware-measured at scope 2026-07-20 and corroborated by docs/hardware/zwo-am5-lx200-protocol.md:177-180. Do NOT "fix" the code toward the docstring by sending R3; do NOT touch `_RATE_TABLE` (:40), whose R3 entry belongs to `move_axis`; do NOT weaken the test assertion `["R2", "Mw", "Qw"]` (test_zwo_am5.py:593). The docstring is what is wrong, not the wire protocol.

---

**DONE (shipped 0.2.44).**

## 32. [minor] Confirmed: on the sim polar provider, Pause publishes `state="paused"` but the sim driver never reads the pause flag -- it keeps streaming errors (still labelled "paused") and finishes with "polar ali

**Files:** server/astrodeck/polar/session.py, server/astrodeck/polar/native.py, server/astrodeck/providers.py, server/astrodeck/api/app.py, ui/src/views/PolarView.tsx, ui/src/store.ts, docs/superpowers/specs/2026-06-15-polar-alignment-design.md

**Fix:**

Make the sim honor the flag it is paused with, and stop `pause()` from asserting a state no driver will keep.

1. server/astrodeck/polar/session.py `_run_sim` (lines 227-252): await a pause check at each yield point -- before the point loop body (:233), and at the top of the convergence loop (:243, before the sleep at :244) -- using the same semantics as polar/native.py:286-290 (`while self._native_paused: await asyncio.sleep(0.1)`). Factor that helper somewhere shared rather than duplicating it. Do NOT change the published field set or the convergence math (`az * 0.82 + jitter`, threshold 0.4, the `state="done"` terminal at :250) -- the sim's numeric behavior is asserted by server/tests/test_polar.py.

2. server/astrodeck/polar/session.py:116-117: the comment "a no-op for NINA/sim, which don't read it" becomes false for sim; update it, and keep it accurate about NINA (the ws action is sent but its acceptance is unverified).

3. server/astrodeck/polar/session.py:115-118 -- add an early return/409 when `not self.running`, so `POST /api/polar/pause` cannot publish `state="paused"` with no session and strand the UI's `running` derivation (PolarView.tsx:31). Same for `resume()` at :126.

4. Add a regression test alongside server/tests/test_polar.py (which today has zero pause/resume coverage -- grep for "pause" in test_polar.py and test_polar_native.py returns nothing): start the sim session, pause during the convergence phase, assert no further `polar` event is published for N ticks and that the session has NOT reached `state="done"`, then resume and assert it completes.

Do NOT "fix" this by hiding or disabling the Pause button for sim in ui/src/views/PolarView.tsx -- the spec (docs/superpowers/specs/2026-06-15-polar-alignment-design.md) makes exercising the full Start/Pa

---

# Still open after the 2026-08-04 sweep

All 32 entries above are closed. These were found WHILE closing them and were
not part of the verified set, so none has been through an adversarial pass.
Ordered worst first.

## A. [important] `/api/discover/*` is gated on `view.status`, so a VIEWER can enumerate the LAN

Pinning the SSRF guard (#9) closed the DNS-rebinding window; it did not answer
why a viewer may scan at all. `GET /api/discover/nina` sweeps the local /24 with
no host argument (`nina.py::_local_subnets`), and `/api/discover/alpaca` probes
any routable host the caller names. Both hold `CAP_VIEW_STATUS`.

The likely fix is raising all four discovery routes to `CAP_CONFIG_BACKEND`,
matching every sibling POST and the fact that the only UI caller is
`DriversPanel` (a Settings surface, `config.backend` in practice). NOT done on
2026-08-04 because it could not be verified end-to-end against the Equipment
screen that night, and breaking device assignment is worse than the leak.
Verify `discoverHardware()` and the connect wizard before changing it.

## B. [minor] `ui/src/api/backends.ts` types `getProfile` as a full `Profile`

After #10 a viewer/operator gets a PARTIAL record (no `host`/`port`/`port_path`,
no `nina_*`/`phd2_*`, no `site_name`). The TS type still promises all of them.
No live flow breaks -- the Equipment editor is `config.backend`-gated, and every
profile WRITE requires that cap, so the read-modify-write in
`ProfileList.onUpdateFromRig` can only run for a holder, who gets the full
record. The type should gain optionals so a future caller cannot be misled.

## C. [minor] An alert sink's `verified` is still cleared when a client OMITS the field

#26 fixed the ordering: a blanked token echoed back no longer reads as a
re-point. But `_merge_alert_verified` keeps whatever `verified` arrived, so a
client that omits the key entirely still clears the badge. The real client
echoes it (`AlertsPanel.tsx` spreads the sink), so this is not the reported bug.

## D. [minor] `POST /api/polar/pause` on an idle session returns 200, not 409

#19 stopped it publishing `state="paused"` with no session -- the part that
stranded the UI. Turning the no-op into a 409 needs a `try/except RuntimeError`
on the two handlers in `api/app.py` AND a UI that expects it; deliberately not
done, since the button only renders while a session runs.

## E. [minor] `GET /api/me` is covered by `IDENTITY_PATHS`, not by the `identity=True` flag

Invariant (4) holds either way (#27). Adding the flag to its `@declare` would be
belt-and-braces against someone renaming the path.

## F. [deliberate] A rig with NO focuser rewrites the last remembered position forever

The carry-forward in #22 has no expiry. Nothing reads the value on such a rig,
and expiring it would reintroduce the blanking bug it exists to prevent.

## G. [deliberate] `on_missed="skip"` does not fire for `start_mode="now"`

A Now target freezes its start at run start, so every target queued behind the
first is "late" by construction and the naive rule silently drops an entire
plan. Bounding a Now target is what stop/max-run already does (#14).

## H. [important] The privacy rule quoted the value it forbids

Not an audit finding -- found by the pre-push scan on 2026-08-04. The standing
constraint ("the real site coordinates and label must NEVER appear in code,
tests, or docs") was pasted into 26 spec/plan docs as boilerplate that SPELLS
OUT both coordinates and the label, 50 occurrences in tracked files. Redacted in
the working tree, but **git history still carries them** -- decide whether that
warrants a history rewrite. `ui/src/lib/__tests__/troubleshoot.test.ts` keeps
the literals ON PURPOSE: it is the guard asserting they never reach an export.

---

# Found by CI after the sweep — FIXED

CI's first run in a day (the last green predates every 2026-08-04 commit) caught
two things no local run could. Recorded because the second one is the most
valuable finding of the whole exercise.

## I. [blocker, FIXED] A FastAPI upgrade silently emptied the RBAC boot assertion

`assert_route_capabilities` iterated `app.routes` and graded every route.
**FastAPI 0.141** changed `include_router` to append ONE lazy `_IncludedRouter`
marker instead of copying the child's routes up. The walk then saw one opaque
object where a dozen graded routes used to be, so `/auth/*`, `/api/visibility`,
`/api/framing`, `/api/survey` and `/api/tiles` stopped being checked at all.

Nothing raised. **The assertion still passed** -- which is what stops anyone
looking. `pyproject` says `fastapi>=0.115` (unpinned), so this was live in every
fresh environment: CI first, then the next deploy. The dev box and the rig are on
0.136, which still flattens, which is why it looked fine locally.

Fixed by `iter_app_routes` (rbac.py), which recurses through the marker's
`original_router` and degrades to the flat list on older FastAPI. Verified on
BOTH shapes -- 0.136 locally and 0.141 in a Linux container. Deliberately NOT
pinned: the guard is a regression test that builds a two-line app, includes a
router, and asserts the child route is visible, so the next shape change fails
loudly instead of narrowing scope in silence.

Note the newly-covered routers all PASS the invariants -- nothing was hiding
behind the gap. The defect was the loss of coverage, not a bad route.

## J. [FIXED] A test that silently required the Rust wheel

`test_polar_alignment_takes_the_camera_before_it_rotates_the_mount` stopped its
run by letting the REAL native fit reject three identical solves. CI's `server`
job does not install that wheel, so `_native` was None and the run died on an
AttributeError. Now stubs the fit alongside the already-stubbed rotation, so it
grades the camera-yield ordering on any host.
