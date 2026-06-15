# AstroDeck Batch 1 — code-review fix list (33 confirmed)

I'll review the consolidated findings and produce the prioritized fix list. The findings are already adversarially confirmed with severity corrections noted inline, so I'll deduplicate, group by corrected severity, and produce the surgical fix list.

# AstroDeck Batch 1 — Prioritized FIX LIST

## SHIP-BLOCKER VERDICT: YES — DO NOT COMMIT BATCH 1 AS-IS

Three **P0 unauthenticated arbitrary-file-write / path-traversal** vulnerabilities (profiles, plans, and the `%5C` backslash path-param read/delete/exfiltrate vector) are confirmed reproduced end-to-end on the running server. These are hard blockers. The single guarded `_path()` fix below closes all three at once and is small. Batch 1 must not be committed until P0-1, P0-2, P0-3 are fixed and the SSRF (P1-1) is at minimum range-blocked.

---

## P0 — SHIP-BLOCKERS (fix before any commit)

**P0-1 / P0-2 / P0-3 — Path traversal: arbitrary `*.json` write, read, delete, exfiltrate (profiles + plans).**
These three findings are one root cause: `ProfileLibrary._path` and `PlanLibrary._path` build `self._dir / f"{id}.json"` with zero sanitization, and `id` is fully client-controllable. Exploitable via request body (`{"id":"../../pwned"}`), and via the backslash path-param vector (`DELETE /api/plans/..%5Cvictim` → 200, deletes `config/victim.json`; `GET /api/plans/..%5Castrodeck` exfiltrates the full AppConfig incl. secret). Starlette collapses `%2F` but not `%5C` on Windows.

- **File:** `server/astrodeck/profiles.py` (`_path`, line 74-75) and `server/astrodeck/plans.py` (`_path`, line 46-47).
- **Change (centralize in each `_path` — covers get/patch/delete/export AND save in one place):**
  ```python
  def _path(self, id: str) -> Path:
      resolved = (self._dir / f"{id}.json").resolve()
      if resolved.parent != self._dir.resolve():
          raise KeyError(id)
      return resolved
  ```
  Use **`raise KeyError`** (not `ValueError`): the profile routes only catch `(KeyError, FileNotFoundError)`, so a `ValueError` would surface as 500; `KeyError` keeps every route at a clean 404.
- **Additionally** in `save_profile` (app.py:395-397) and `save_plan` (app.py:447-454): do **not** trust a client `id` for new records — only honor a client-supplied `id` when `self._path(id)` already exists (upsert); otherwise mint the uuid server-side. `import_plan` is already safe (re-keys with fresh uuid).
- **Why:** Unauthenticated arbitrary-file write/read/delete on a `0.0.0.0`-bound server with no auth. Can overwrite the server's own `astrodeck.json` (corrupt/hijack site config), exfiltrate the AppConfig, or delete arbitrary `*.json`. Critical.
- **Note for implementer:** `hub._capture_path` (hub.py:429) already uses an allowlist sanitizer — the same pattern simply was never applied to profile/plan ids. Optionally add a uuid-format allowlist as defense-in-depth, but the resolved-parent assert is the load-bearing fix.

---

## P1 — fix before commit (functional/correctness regressions, real defects)

**P1-1 — SSRF + port/host scanner oracle on `/api/discover/alpaca` (and `/api/discover/nina`).**
- **File:** `server/astrodeck/devices/alpaca.py` (`query_server`, 97-125); validation entry at `server/astrodeck/api/app.py` (191-204). Same class affects `server/astrodeck/devices/nina.py` `discover_nina` extra_hosts (740-752).
- **Change:** Add a shared host-validation helper used by both discover endpoints. Resolve the host and **reject the resolved IP** (not just the literal — defeats DNS rebinding) if it is loopback/private/link-local/reserved, unless explicit opt-in; at minimum hard-block `169.254.0.0/16` and the metadata IP. Validate host is a bare hostname/IP (block `evil.com/x?`, `user@`, embedded `:port`). Reject non-numeric/oversized ports. Keep the 5s timeout. Do **not** echo the upstream HTTP status verbatim (it's a scan oracle — alpaca.py:118 → app.py:199).
- **Why:** Unauthenticated SSRF on a `0.0.0.0`-bound, no-auth server; reaches internal services / cloud metadata; differentiated errors form a precise scan oracle. P1 (not P0) because it's a fixed-path GET, LAN tool, 5s-capped, no body exfil. Scheme is already hardcoded `http://` — no scheme work needed.

**P1-2 — Plate-solve passes DIAGONAL FOV to ASTAP's `-fov`, which expects VERTICAL (height).**
- **File:** `server/astrodeck/hub.py`, line 479 (in `solve_and_sync`).
- **Change:** `fov_hint = opt["fov_h_deg"] or None` (was `opt["fov_diag_deg"]`). `fov_h_deg` is already computed/exposed by `effective_optics` (hub.py:228). If a robustness margin is wanted, inflate `fov_h_deg` slightly — do not substitute the diagonal.
- **Why:** Diagonal is ~1.2–1.8× larger than height; ASTAP narrows its scale search around `-fov`, so an over-large hint degrades/occasionally fails real solves. The unused helper `astap.py:_fov_from_scale` documents height is the intended dimension. Sim solver ignores the hint, so tests don't catch it.

**P1-3 — Below-horizon preflight wrongly rejects calibration-only plans (darks/bias/flats).**
- **File:** `server/astrodeck/api/app.py`, sequence_start preflight loop (727-737).
- **Change:** Add `if t.calibration: continue` before the `_horizon_block(ra, dec)` call (t is a `Target`, so the attribute is exact). The mandatory dummy `ra_hours`/`dec_deg` on calibration targets never hit the `None`-skip, so a dark/bias-only plan at a configured site whose dummy coords are below horizon is 409'd and can't start. Engine routes calibration to `_run_calibration` which never slews/centers, so the horizon check is meaningless for them. GOTO path uses a separate body with no calibration field — no mirror needed today.
- **Why:** False-positive 409 blocks a routine workflow (indoor/parked dark-library build); `(0,0)` is below horizon for a large share of sites/times.

**P1-4 — Re-render storm: 6 high-traffic views never got the selector split (incomplete lane-1D fix).**
- **Files:** `ui/src/views/CaptureView.tsx:8`, `FocusView.tsx:8`, `GuideView.tsx:8`, `MountView.tsx:10`, `PolarView.tsx:7`, `PowerView.tsx:8`. (This is the dedup of the two duplicate findings — frontend-correctness + regression-risk — same root cause and same fix.)
- **Change:** Replace each selector-less `const {...} = useStore()` with narrow per-slice selectors. Reuse the already-exported hooks in `store.ts:477-482`: `useStatus()/usePreview()/useFocus()/useGuide()/usePolar()`, plus `const showToast = useStore(s => s.showToast)` (stable action ref; `useShowToast` does **not** exist — do not import it).
- **Why:** `ws.ts:37` calls `noteWsEvent()` → `set({wsLastEvent: Date.now()})` on every WS frame; a no-arg `useStore()` subscribes to the whole state and re-renders on every frame. Only App/ConnectView/SequenceView were migrated, leaving exactly the live imaging/guiding screens broken — the batch's own stated §13/Risk-14 perf goal, half-delivered. Drop the "strictly worse than before" framing; justify purely as completing the split. P1 (not P0): `key={view}` mounts one view at a time, React batches, output is correct — wasted renders/jank only.

**P1-5 — LogDrawer focus-trap broken on the primary desktop (lg+) layout.**
- **File:** `ui/src/components/LogDrawer.tsx`, lines 118 and 139 (both nodes share `panelRef`). (Dedup of the two LogDrawer findings.)
- **Change:** Split into two refs (`deskRef`/`sheetRef`). Both `role=dialog` nodes are always mounted (Tailwind `hidden lg:flex` / `lg:hidden` = display:none), so the shared object ref last-write-wins to the mobile sheet; on lg+ that node is `display:none`, so `focusables()[0]?.focus()` is a no-op and Tab never traps. Run the focus effect against the **visible** node. **Do NOT use `offsetParent`** to discriminate — the sheet is `position:fixed` so `offsetParent` is null even when visible. Use `getClientRects().length > 0` (or `checkVisibility()`), or gate mounting with a JS `lg` media-query so only the active node renders. Escape and return-focus are NOT broken (global listener + `returnFocusRef`) — only move-focus-in and trap.
- **Why:** An explicitly-spec'd dialog a11y behavior (spec §3/§16) is fully non-functional for keyboard users on the primary docked layout. P1; mouse/Escape still work so not P0.

---

## P2 — fix this batch (latent contract/durability/perf defects; no live user harm yet)

**P2-1 — `horizon_min_deg` destructive round-trip on `PUT /api/site`.**
- **File:** `server/astrodeck/api/app.py` `put_site` (319-321); contract gap at `ui/src/types.ts:236`.
- **Change (minimal):** when `body.horizon_min_deg is None`, preserve existing: `site = site.model_copy(update={"horizon_min_deg": config_store.cfg().site.horizon_min_deg})`. **Preferred structural fix:** drop `horizon_min_deg`/`is_default` from the round-tripped `Site` contract and have `set_site` read-merge server-owned onboarding fields from existing `cfg.site` — closes the clobber permanently. Note: `hub.mark_site_configured()` (hub.py:184-193) is dead code meant for exactly this.
- **Why:** TS `Site` omits `horizon_min_deg`, so a client-echoed site lets pydantic default it back to 15.0, silently wiping a custom minimum altitude. Latent (no save UI yet) but contract is frozen-broken.

**P2-2 — Blocking disk read on the asyncio loop every 2s via `effective_optics`.**
- **File:** `server/astrodeck/hub.py` (`effective_optics` line 200; called from `poll_status` 631 & 640, and `summary` 168 — actually 2 reads/cycle).
- **Change:** Cache the active `Profile` in memory; invalidate on `set_active_profile`/save/delete. `apply_profile` (hub.py:332) already holds the `Profile` object to seed the cache for free; this also removes the redundant second read. (`asyncio.to_thread` is a weaker fallback — adds churn and leaves the double-read.)
- **Why:** `profiles.active()` → `Path.read_text()` synchronously on the event loop twice per 2s cycle when a profile is active; on the field Raspberry-Pi/SD-card target under FITS-write contention this can stall WS telemetry.

**P2-3 — Synchronous config writes (with blocking `time.sleep` retry) on the event loop.**
- **File:** `server/astrodeck/hub.py` (`set_site`/`set_optics`/`apply_profile` 188-194, 296-333); `server/astrodeck/api/app.py` (316-360); `server/astrodeck/persist.py` retry path.
- **Change:** Offload disk writes via `asyncio.to_thread` from the async paths (or make `ConfigStore` expose an async save), covering the profiles/plans writers too, so the `time.sleep`-retry never runs on the loop. Add a combined setter (e.g. `set_optics_and_active_profile`) so `apply_profile` does a single bump+write instead of two.
- **Why:** `_replace_with_retry` blocks up to ~0.72s on Windows AV/indexer `PermissionError`; called directly from async handlers, this freezes the whole loop (all WS + in-flight requests) per save on the documented Windows target.

**P2-4 — Atomic-write durability: live config file can vanish on power loss; no fsync; no `.bak` recovery.** (Dedup of the two persist.py durability findings — same root cause.)
- **File:** `server/astrodeck/persist.py` (`write_json_atomic`, 60-68) + `server/astrodeck/config.py` (`_load` 122-129, `_recover_from_corrupt` 146-155).
- **Change:** (a) Write `.tmp` first and `os.fsync` it (and ideally the parent dir) before `os.replace`; (b) create `.bak` by **COPY** (`shutil.copy2`), not move, so the primary is never absent; (c) in `_load`, on `FileNotFoundError` **or** unparseable, attempt restore from `.bak` **before** falling back to defaults — and **validate** the `.bak` parses first, because `_recover_from_corrupt` deliberately stashes the corrupt file at `.bak` (read `.bak` before overwriting it).
- **Why:** Crash between the backup-move and tmp-replace leaves no live config → `_load` silently resets to lat0/lon0 defaults (the Gulf-of-Guinea wrong-location class this batch's P0 fixed), and auto-save then erases the recoverable `.bak`. The docstrings even claim "copies" while the code moves. Narrow power-loss window but severe, silent blast radius.

**P2-5 — `apply_profile` returns a non-`RigStatus`-shaped `summary` (frozen contract violated).**
- **File:** `server/astrodeck/hub.py` (`apply_profile`, ~335); contract `ui/src/types.ts:346`.
- **Change:** `return {"summary": await self.poll_status(), "results": results, "connected": ok, "total": len(results)}` — `poll_status` emits `connected`/`looping`/`mode`/`site`/`optics`/`busy` (a real `RigStatus`). Devices are already connected at that point, so the device I/O is fine. Prefer this over the explicit-build alternative (which omits optional `site`/`optics`/`busy`).
- **Why:** `summary()` emits `devices` (no `connected`/`looping`); `ApplyResult.summary` is typed `RigStatus`. Latent (the apply route is fire-and-forget `{started}`, result discarded today), but breaks the moment a profiles view consumes the apply result. Related sites app.py:258/847 ship the same shape — out of scope here.

**P2-6 — `RigStatus.site` contract is a 3-field subset of the 6-field `poll_status` site; `store.site` goes stale while connected.**
- **File:** `ui/src/types.ts:57` vs `server/astrodeck/hub.py:636`; handler `ui/src/store.ts` status case (377-381).
- **Change (prefer variant 1):** Widen `RigStatus.site` in types.ts to the full `{name,latitude,longitude,elevation_m,is_default,horizon_min_deg}`, and have the `status` handler also `set({ site: status.site })`. (The config-handler alternative is weaker — `AppConfig.site` is the 4-field persisted type lacking `is_default`/`horizon_min_deg`.)
- **Why:** `store.site` (typed `SiteInfo`) is set only from the `hello` event, so saving a site while connected won't flip `is_default`/refresh horizon until reconnect. Latent — only consumer is `buildPreflight` (test-only today). P2.

**P2-7 — SequenceView is a second writer of `astrodeck-plan` (SSOT violation, armed data-loss race).** (Dedup: the P2 frontend-correctness finding and the P3 regression-risk finding are the same defect; keep at P2.)
- **File:** `ui/src/views/SequenceView.tsx` (own `loadPlan`/`DEFAULT_PLAN` 21-27, `useState(loadPlan)` 52, persistence effect 58-60).
- **Change:** Migrate to the store: `const plan = useStore(s=>s.plan); const setPlan = useStore(s=>s.setPlan);` (setter already persists). Delete SequenceView's private `loadPlan`, `DEFAULT_PLAN`, the `useState`, and the `localStorage.setItem` effect so the key has a single writer. **Reconcile defaults:** move SequenceView's intended values into the store's `defaultPlan()` (store's defaults differ: name "Untitled plan" vs "Tonight", guide false vs true, cool_to null vs -10, meridian_flip false vs true, missing `dither_pixels:3`) since `loadPlan` spreads `{...defaultPlan(), ...parsed}`.
- **Why:** Two independent writers of one localStorage key with divergent defaults; store `plan`/`usePlan`/`setPlan` are dead today (zero consumers), so no live conflict — but the moment a Batch-2 atlas/profile path calls `store.setPlan`, SequenceView's next keystroke clobbers it, reintroducing the exact SSOT race the reconciliation was meant to kill. Directly contradicts a stated Batch-1 architectural goal.

**P2-8 — Unbounded growth + O(n) disk fan-out on profile/plan create.**
- **File:** `server/astrodeck/profiles.py` (`_all`/`name_exists` 77-91, 105-106); `server/astrodeck/plans.py` (53-71, 94-101).
- **Change:** Cap stored profiles/plans (reject save at `>= ~500` with 409). Make profiles' `name_exists` **stream** like plans' (iterate `list_json` + `read_json_or`, return on first name match) instead of going through `_all()` — the finding's "profiles' `any(...)` is fine" is wrong: `_all()` eagerly reads+parses every file first. Optionally a small in-memory name index. Per-IP rate-limit is low-value given single-user trust model.
- **Why:** No quota/eviction; `name_exists` (called per non-overwrite save) does full-dir read+parse. A client can fill disk and degrade every save/list linearly. P2: degraded UX, not RCE/disclosure.

**P2-9 — Unhandled 500 on malformed `schema_version` in plan import.**
- **File:** `server/astrodeck/api/app.py:476` (and mirror at `server/astrodeck/plans.py:121`).
- **Change:** Wrap the coercion before the `>` check:
  ```python
  try:
      ver = int(raw.get("schema_version", 1) or 1)
  except (TypeError, ValueError):
      raise HTTPException(422, detail={"detail": "invalid schema_version", "code": "invalid"})
  if ver > PLAN_SCHEMA: ...
  ```
  Mirror in `plans.py:121` (`raise PlanImportError("invalid schema_version", "invalid")`).
- **Why:** `{"schema_version":"abc"}` → `ValueError`, `{"schema_version":[9]}` → `TypeError`, both uncaught (outside the try) → unauthenticated 500 + stack-trace noise where 422 is intended. Downgraded P1→P2 (no corruption/crash/DoS).

---

## P3 — cleanup / polish (do if cheap; not commit-gating)

**P3-1 — `alpaca.py` mislabels read timeouts as "unreachable."**
- **File:** `server/astrodeck/devices/alpaca.py` (~108-110). Split out `except httpx.TimeoutException → kind="timeout"` before the `(ConnectError, TransportError) → "unreachable"` branch; fold the now-redundant `except httpx.ConnectTimeout`. `ReadTimeout` (host reachable but hung) currently gets "check the IP… device is on" — wrong copy.

**P3-2 — Heartbeat/disconnect race can flip `_bridge_ready` true after teardown.**
- **File:** `server/astrodeck/hub.py` (`_nina_heartbeat`, 282-291). Add `if self.nina_client is None: return` inside the try right before `self._bridge_ready = True` (line 288). Benign, self-correcting; minimal guard.

**P3-3 — Toast sweeper doesn't batch; `dismissExpired` is dead code.**
- **File:** `ui/src/components/Toasts.tsx` (89-98). Replace the per-id `dismissToast` loop with a single `useStore.getState().dismissExpired()`; drop the local expiry filter. Matches docstring/spec; removes dead code. (React 18 already auto-batches, so user-facing impact is near-zero — this is cleanup + spec conformance.) This is the dedup of the two Toasts-sweeper findings.

**P3-4 — Dead backward-compat single-toast mirror.**
- **File:** `ui/src/store.ts`. Remove the `toast` field (141, 219), `latestCompat` (460-464), `toastKey` (177), and the four `toast: latestCompat(...)` writes (301, 323, 329, 337). Keep the `Toast`/`ToastLevel` imports and the unrelated local `const toast: Toast = {...}` at line 305. Stale comment (458-459) references an already-completed swap.

**P3-5 — Warn-tier LEDs have no static cue under prefers-reduced-motion (HealthLeds + ConnectionBanner).** (Dedup of the two LED findings.)
- **File:** `ui/src/index.css` (~185 `.led-warn`; reduced-motion block 249-252). Give `.led-warn` a distinct static ring/outline mirroring `.led-bad`'s halo (index.css:221-224) so warn vs ok is shape-distinguishable when blink is disabled. Both `.blink` and `.blink-alert` are killed under reduced-motion but only `.led-bad` keeps a static halo. Low impact — label+color redundancy already present; this closes the component's own promised per-severity dot cue.

**P3-6 — `store.ts` `hello` handler has dead `data.summary` path.**
- **File:** `ui/src/store.ts:386`. Drop the non-existent `data.summary ?? data` branch; treat `data` directly as the summary dict and fix the misleading comment. Backend sends `{"data": hub.summary()}` (no wrapper). Reject the "wrap in `{data:{summary:...}}`" alternative — needless churn; standardize on unwrapped (matches `/api/summary`).

**P3-7 — `store.config` never hydrated at boot.**
- **File:** `ui/src/store.ts` (`loadConfig` 232-239); call site `ui/src/ws.ts` onopen (preferred) or `App.tsx` mount effect. Add a fire-and-forget `st.loadConfig()` in `ws.ts` onopen next to `reconcileLogs()` (also re-hydrates after reconnect). Latent until the Settings view lands — would otherwise show blank/defaults until a config mutation.

**P3-8 — `_replace_with_retry` off-by-one (4th `os.replace`).**
- **File:** `server/astrodeck/persist.py` (38-50). Restructure to `for attempt in range(_REPLACE_RETRIES): try: os.replace(...); return; except PermissionError as e: last=e; sleep(...)` then `raise last`. Removes the redundant post-loop call; makes retry count exact.

**P3-9 — `POST /api/discover/alpaca` advertises a body API but uses query params.**
- **File:** `server/astrodeck/api/app.py` (201-204). Either drop the POST alias (unused; GET covers it) or add `class AlpacaScanBody(BaseModel): host:str; port:int=11111` and read from it. Note: this does NOT harden the SSRF — that fix lives in P1-1 and applies to GET too.

**P3-10 — Coalesced-toast count + per-card subscriptions (optional).**
- **File:** `ui/src/components/Toasts.tsx` (49-54, 36-37). Optionally fold count into the announced title when `count>1` (keep chip `aria-hidden`) so a flapping error isn't SR-silent; optionally hoist `dismissToast`/`openLog` to parent and pass as props. Pure polish.

---

## Suggested execution order
1. **P0-1/2/3** (single guarded `_path` in both libraries + server-side id minting) — unblocks commit.
2. **P1-1** SSRF host validation (shared helper for alpaca + nina).
3. **P1-2, P1-3** (backend one-liners), **P1-4, P1-5** (frontend selector split + LogDrawer refs).
4. **P2** batch (durability P2-4 and the contract dedups P2-1/5/6/7 are highest-value).
5. **P3** polish as time permits.

**Dedups applied:** LogDrawer (2→1, P1-5), 6-view re-render storm (2→1, P1-4), persist.py durability (2→1, P2-4), SequenceView SSOT (2→1, P2-7), Toast sweeper (2→1, P3-3), reduced-motion warn LED (2→1, P3-5). **Severity corrections honored** from the confirmed notes: P2-1, P2-5, P2-6, P2-9 downgraded from P1; P2-4 raised from P3; P3-3 downgraded from P2.
