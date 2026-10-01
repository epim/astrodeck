# AstroDeck site content audit

Baseline: `bf3eaddd`, reviewed 2026-10-01. This audits the five existing `site/*.html` pages at that commit, including repeated feature tables, diagrams, metadata and setup text. Line references intentionally identify the old source, not the rewritten page. Repeated factual claims are grouped where they make the same assertion. Navigation labels, decorative SVG geometry and purely editorial headings carry no separate technical claim; all factual text in their captions is covered below.

Status vocabulary: Retain means the bounded claim is implemented; Qualify means scope/prerequisites were missing; Stale means later source changed it; False means current code contradicts it; Cut means no useful supported assertion remains. Evidence IDs expand to exact repository file:line references in `claims-ledger.md`. Both implementation and existing regression tests were read. Tests and fresh installs were not run by this copy task.

## Highest-impact corrections

- Native guiding and native autofocus require `astrodeck_native`, which published releases do not include yet. Claude confirmed this as #630. ASTAP and a star database are separate prerequisites.
- DSS2 is not bundled or seeded. Atlas offers an offline schematic sky, and the operator can fetch survey tiles inside Atlas for personal use. The stale workflow fetch is tracked as #631.
- Forecast rain can veto automatic session re-arming; forecast cloud does not. The old site says overcast blocks the night.
- Native-driver support and recorded hardware checks are separate. The new Verified label describes the exact check, never full-night certification. AM5N, ASI220MM, Poseidon-M Pro and Snowflake have bounded recorded checks; EAF/CAA and other SDK-enumerated models remain Supported.
- The NINA backend now exposes a switch role, and does not expose dome, cover-calibrator or safety-monitor roles. Alpaca has no registered ObservingConditions/weather-station adapter.
- TARGET now owns centring and mosaic panels. SLEW + CENTER remains only for saved legacy graphs. The old palette and outputs no longer describe the editor.

## Existing overview page

| Baseline source | Claim group | Finding | Decision and evidence |
|---|---|---|---|
| site/index.html:7 | Metadata promises whole-night control and cloud knowledge. | Qualify | CORE, SEQUENCE, CLOUD. Describe implemented tools, not guaranteed autonomous completion. |
| site/index.html:43 | Night runs itself; operator only watches; slew, centre, focus, guide, capture, dither, flip, dawn park, report. | Qualify | SEQUENCE, REPORT, PROVIDERS, ASTAP. Provider/install prerequisites and failure states make the blanket promise too strong. |
| site/index.html:46 | Cloud knowledge lets a target stay clear. | Qualify | CLOUD, FRAME_CLOUD. Use estimates and conditions, not a clear-sky guarantee. |
| site/index.html:51 | Run in two minutes. | Cut | No timed clean-machine installation evidence. |
| site/index.html:59 | 21 Flow types, 250 MB offline sky, 11 simulated devices, 3 rig routes, 0 forwarded ports. | Stale/qualify | FLOW: one type is now legacy. OFFLINE: DSS2 is fetched by operator, not bundled. SIM: omit device count. ASIAIR: experimental. REMOTE: requires configured service; drop numeric marketing. |
| site/index.html:72 | Shared device abstraction permits swapping camera or implementing small async driver. | Retain bounded | DRIVERS, EXTENSIONS. Keep plugin/adapter fact, remove claim that adding a backend never touches existing code. |
| site/index.html:83 | Native vendor list; everything else via Alpaca/Windows ASCOM. | Qualify | DRIVERS, ALPACA, COM. Installed drivers/SDKs and supported device classes matter; everything else is false. |
| site/index.html:93 | Star detection, autofocus, solving, polar alignment, guiding and sequencing all bundled; no other astronomy software. | False | PROVIDERS, ASTAP. Native engine absent in published releases (#630); ASTAP and database separately needed. |
| site/index.html:103 | NINA and ASIAIR add identical UI, Flows, sessions and remote control. | Qualify | NINA, ASIAIR. Equal presentation is appropriate; hardware capability parity is not. ASIAIR is optional experimental protocol backend. |
| site/index.html:114 | Ships a 250 MB survey pack; automatic offline fallback. | False | OFFLINE. Packager/runtime deliberately refuse DSS2 redistribution; use operator fetch plus schematic fallback. |
| site/index.html:122 | Outbound relay, no port forwarding, every tunneled request authenticated. | Retain bounded | REMOTE, AUTH. Describe optional outbound relay, without a universal zero-configuration promise or security guarantee. |
| site/index.html:128 | Apache-2.0 source; external plugin packages. | Retain | LICENSE, EXTENSIONS. Third-party notices remain applicable. |
| site/index.html:147 | Entire illustrative night can run while asleep. | Cut guarantee | SEQUENCE. Timeline is illustrative, not a recorded verification. Arbitrary times/temperature/dither labels are removed with the schematic. |
| site/index.html:210 | Quality failures always re-shot and work owed at dawn always carries automatically. | Qualify | SESSION. Accepted-quota/repeat settings and rejection bounds matter; new and legacy flow counting differ. |
| site/index.html:220 | Flip always verifies pier side, retries at meridian and yields correctly oriented night. | Qualify | SEQUENCE; engine.py:12481 implements conditional flip handling. Remove guaranteed overnight outcome. |
| site/index.html:233 | Every frame has HFR/stars/RMS, manual override, dusk re-arm, finished stack after month. | Qualify/cut | SESSION, DUSK, REPORT. Metrics may be absent, re-arm must be configured; no completion-date or finished-stack promise. |
| site/index.html:246 | Eleven screens. | Cut | UI. Navigation now has classic and newer layouts; fixed count is not useful. |
| site/index.html:252 | Capture preview, zoom, loupe, histogram/stretch, star/clip overlays, filmstrip, cooler and heater. | Retain bounded | CAPTURE, ZWO_CAMERA, PLAYER_CAMERA. Cooling/heater depend on actual device capability. |
| site/index.html:256 | V-curve/hyperbola fit, filter offsets, temperature/frame-count refocus, Bahtinov aid. | Retain subset | FOCUS, PROVIDERS. Public copy keeps focus/curve/refocus; avoids detailed all-provider fit and tool parity. |
| site/index.html:260 | Touch mount pad, dead-man switch, guarded goto, solve/sync, rate/park controls. | Retain subset | SEQUENCE, ASTAP. Controls are implemented but depend on adapter capabilities; remove exhaustive parity promise. |
| site/index.html:264 | Native/PHD2/NINA guiding, graph/RMS/dither, assistant seeing/backlash. | Retain subset | GUIDE. Public copy keeps providers/graph/dither; assistant availability is not asserted across routes. |
| site/index.html:268 | Offline framing and mosaics on survey imagery. | Qualify | ATLAS, MOSAIC, OFFLINE. Download image pack first; schematic remains available. |
| site/index.html:272 | Tonight ranking, difficulty, visibility, transit, twilight and moon separation. | Retain subset | TONIGHT. No private or location-derived values appear in marketing content. |
| site/index.html:276 | Monitor ETA/progress/cooling/guiding/flip/HFR/thumbnail/dome plus red mode. | Retain bounded | MONITOR, UI. A screenshot is labelled with its actual idle simulator state. |
| site/index.html:280 | Native TPPA and guide-scope offset. | Qualify | PROVIDERS. Native dependency requirement replaces unconditional feature availability. |
| site/index.html:284 | FITS headers/WCS, night report and bundle sorted for external stackers. | Retain subset | CAPTURE, REPORT, BUNDLE. Keep report/export outcome; matching masters and processing are not guaranteed. |
| site/index.html:299 | Three permanent routes; standalone whole stack; NINA:1888; ASIAIR same features. | Qualify | NINA, ASIAIR, PROVIDERS. Equal route cards with distinct scope, default port and experimental limitations. |
| site/index.html:339 | Two-minute try; simulator connects 11 devices in three seconds. | Cut timings | SIM. Simulator action remains; no stopwatch/device-count promise. |
| site/index.html:350 | Source install commands alone suffice; localhost:8800. | Stale/qualify | SOURCE, LAN. New source steps build UI and explain extras; local default is correct. |
| site/index.html:357 | Docker multi-arch/Pi, single binaries on Windows/Linux/Mac with no Python/Node. | Retain subset | RELEASE, WINDOWS. Exact executable target architectures listed; Docker universal-host impression removed. |
| site/index.html:368 | Simulator focuses, solves, guides full two-target night; blackout filter response. | Qualify | SIM, PROVIDERS. Keep responsive synthetic frames; optional native engine means not all operations come with bare install. |
| site/index.html:391 | v0.3; used most clear nights; guiding/solve sharp corners; no flats wizard. | Retire | No release-complete claim. CALIBRATION describes positive implemented work instead of frozen absence or unsourced use-frequency. |
| site/index.html:398 | ASIAIR never touched hardware; GOES Americas; unsigned binaries always warn. | Qualify | ASIAIR tests are fake; no broader transport-history assertion. CLOUD_COVERAGE footprint qualified. RELEASE/UPDATE distinguish artifact signatures from OS signing; no universal warning claim. |

## Existing features page

| Baseline source | Claim group | Finding | Decision and evidence |
|---|---|---|---|
| site/features.html:7 | Feature-matrix metadata and all routes share the same functions. | False as blanket | CORE, NINA, ASIAIR, PROVIDERS. Replace universal yes/no matrix with capability-dependent feature overview. |
| site/features.html:73 | All routes expose/download FITS, cooling, warm ramp; standalone/ASIAIR dew heater. | Qualify | CAPTURE, ZWO_CAMERA, PLAYER_CAMERA, NINA, ASIAIR. Native ZWO lacks cooler/dew; route label alone does not imply device capability. |
| site/features.html:77 | All routes have identical mount slew/sync/park/jog/tracking/drive rate/pier/flip surface. | Qualify | SEQUENCE, NINA, ASIAIR. ASIAIR role adapter and busy limits differ; no universal control parity. |
| site/features.html:80 | Focuser/temp all; filterwheel/rotator standalone+NINA; ASIAIR not yet. | Retain bounded | ACCESSORIES, NINA, ASIAIR. Unsupported ASIAIR roles omitted; no roadmap promise implied by 'not yet'. |
| site/features.html:83 | NINA dome/shutter, cover calibrator and safety monitor; NINA has no power switch. | False | NINA role tuple at nina_backend.py:139 includes switch, excludes those three roles. |
| site/features.html:85 | Standalone power; ASIAIR four power ports; safety standalone/NINA. | Qualify/correct | ALPACA, ASIAIR, NINA. Device-dependent switching; no public port-count claim; correct NINA safety absence. |
| site/features.html:91 | ASIAIR wheel/rotator mapping unconfirmed, avoids guesses. | Retain boundary | ASIAIR. Say unsupported experimental roles, without stating future timing or full on-box validation. |
| site/features.html:114 | All routes autofocus, filters/thermal refocus, solving/WCS, TPPA, native/PHD2/NINA/ASIAIR guide behavior. | Qualify | PROVIDERS, FOCUS, ASTAP, GUIDE. Separate provider prerequisites and device controls; do not repeat universal yes cells. |
| site/features.html:123 | Guiding assistant across standalone/NINA, dither, satellite-rejecting stacking, quality/cloud all routes. | Qualify | GUIDE, STACK, SESSION, FRAME_CLOUD. Keep selected supported claims; no assistant/stack quality/cloud infallibility or route parity promise. |
| site/features.html:147 | 21 Flow types/two wires/compiler checks. | Stale count | FLOW, CHECKS. Two wire kinds remain; current palette omits legacy SLEW. |
| site/features.html:148 | Full autonomous sequence. | Qualify | SEQUENCE, PROVIDERS. Defined controls, no guaranteed completion. |
| site/features.html:149 | Multi-night session re-arms itself; every frame metrics and manual override. | Qualify | SESSION, DUSK. Explicit configuration and optional metrics. |
| site/features.html:151 | Atlas exact TAN projection, rotatable FOV, mosaics; bundled offline pack. | Mixed | ATLAS/MOSAIC retained; OFFLINE bundle claim false. Projection detail omitted from visitor copy. |
| site/features.html:153 | Tonight ranking/difficulty/visibility; forecast/cloud dome. | Retain bounded | TONIGHT, WEATHER, CLOUD_COVERAGE. |
| site/features.html:155 | Cloud holds track continuously, darks, recool/recentre/refocus. | Qualify | CLOUD_HOLD. Hold may park for other conditions; device/provider-dependent recovery. |
| site/features.html:156 | Monitor; red mode remembers day/night brightness. | Retain subset | MONITOR, UI. No fixed screen counts. |
| site/features.html:158 | Notifications and dead-man heartbeat; relay without router config. | Retain subset | ALERTS, REMOTE. Configure sinks/relay; avoid universal availability. |
| site/features.html:160 | Roles/local+Google/share links; precise/derived data cannot leak. | Retain bounded/cut absolute | AUTH. Role/sign-in facts retained; absolute privacy claim removed. |
| site/features.html:163 | Ed25519 fail-closed update, rollback, never interrupts sequence. | Qualify | UPDATE. Supervised signed update path; distinguish binary replacement and deployment restarts. |
| site/features.html:164 | Report/reason metrics; calibrated pre-sorted stacking zip; simulator fidelity. | Retain bounded | REPORT, BUNDLE, SIM. No automatic processing or every-master guarantee. |
| site/features.html:183 | No flats wizard, never-on-hardware ASIAIR, global forecast/safety, sharp corners, unsigned warnings. | Retire/qualify | CALIBRATION, ASIAIR, CLOUD_COVERAGE, UPDATE. Replace stale absence list with explicit current prerequisites and experimental limits. |

## Existing hardware page

| Baseline source | Claim group | Finding | Decision and evidence |
|---|---|---|---|
| site/hardware.html:7 | Every vendor supported; bring existing gear; native needs nothing installed; most rigs use native. | False/unsupported generalization | DRIVERS, COM, PLAYER_CAMERA. Driver/SDK requirements and model-specific evidence replace 'most' and 'every'. |
| site/hardware.html:55 | No vendor software, plug USB and connect. | Qualify | PLAYER_CAMERA: SDK must be fetched. USB/platform permissions and vendor dependencies still apply. |
| site/hardware.html:68 | AM5/AM5N Verified. | Split evidence | HARDWARE_CHECKS. Recorded native validation names AM5N; other AM5 family is Supported only. |
| site/hardware.html:69 | ASI220MM Verified; all other uncooled ASI Supported. | Qualify | ZWO_CAMERA. Verified label means documented SDK/device checks, not full-night proof; other SDK-enumerated models Supported. |
| site/hardware.html:71 | Cooled ASI no native cooler, use Alpaca. | Retain limit | ZWO_CAMERA. Native cooler/dew absence proven; Alpaca only if matching driver exists. |
| site/hardware.html:72 | EAF Verified, CAA Supported. | Narrow evidence | ACCESSORIES. Inventory proves identification. Label Supported here unless an actual native operation record is cited; no whole-night claim. |
| site/hardware.html:74 | ASIAIR supported protocol backend, hardware untested. | Qualify | ASIAIR. Experimental only with exact roles and dependency, no official API. |
| site/hardware.html:75 | Poseidon-M Pro Verified, any other Player One Supported with every control. | Qualify | PLAYER_CAMERA. Specific SDK property/gain/read-mode checks recorded; cooling capability follows model; SDK fetch required. |
| site/hardware.html:77 | Snowflake serial protocol Verified. | Retain narrow | ACCESSORIES. Hardware protocol check documented; not universal nightly reliability. |
| site/hardware.html:82 | Verified means whole unattended nights; Supported SDK reach means no night yet; no native ZWO EFW. | False definition/retain driver absence | HARDWARE_CHECKS, DRIVERS. Use explicit recorded-check definition, not inferred usage history. No native EFW is listed among entry points. |
| site/hardware.html:96 | If ASCOM, works; all vendors/endpoints and weather stations. | False/overbroad | ALPACA. Registered device classes only; no ObservingConditions adapter. |
| site/hardware.html:112 | Managed COM host requires no ASCOM Remote; absent Linux/Mac. | Retain qualified | COM. COM dependency and installed ASCOM driver required; no bundled dependency assumption. |
| site/hardware.html:127 | ASTAP/HFR, guider options, native TPPA. | Qualify | ASTAP, PROVIDERS, GUIDE. Add missing installation prerequisites. |
| site/hardware.html:130 | Open-Meteo/Astrospheric, GOES, DSS offline, notifications, host platforms. | Qualify | WEATHER requires account acknowledgement for Astrospheric; OFFLINE fetched pack; RELEASE explicit architecture targets, no all-platform peripherals guarantee. |
| site/hardware.html:148 | Single abstraction makes backends interchangeable and adding one never changes higher code. | Qualify | EXTENSIONS, NINA, ASIAIR. Interface is real; capability parity/ease/zero-change guarantee is not. |
| site/hardware.html:212 | Ask and device gets added; handful async methods; contract test guarantees behavior. | Cut guarantees | EXTENSIONS. Invite issue with model/protocol; no delivery commitment or proof from tests alone. |
| site/hardware.html:242 | External packages register entry point and appear in equipment list. | Retain | EXTENSIONS. Discovery is version/contract-gated. |
| site/hardware.html:264 | Not affiliated with listed vendors/projects. | Retain context | LICENSE. Product naming states compatibility and makes no endorsement claim. |

## Existing Flows page

| Baseline source | Claim group | Finding | Decision and evidence |
|---|---|---|---|
| site/flows.html:7 | Metadata/simple month planning; canvas sequence and reaction diagram. | Retain subset | FLOW. Replace legacy-node diagram with a current schematic or real simulator screenshot. |
| site/flows.html:192 | Old diagram and caption imply exact runnable plan/settings. | Replace | MOSAIC, CHECKS. New TARGET semantics and report event require current topology. |
| site/flows.html:212 | Solid order wires; canvas refuses a night with no ending. | Qualify | FLOW, CHECKS. Structural checks exist; no claim every invalid plan is impossible to draw. |
| site/flows.html:228 | Dashed event reactions; campaign repeats for weeks with one wire. | Qualify | FLOW, DUSK. Wiring plus repeat/quotas/recovery settings required. |
| site/flows.html:238 | Interleaving always yields stackable color image at every stopping point. | Qualify | FILTER_CYCLE. Exposure balance fact retained; processed-image guarantee removed. |
| site/flows.html:252 | Safety abort vs cloud hold and full restart recovery. | Retain bounded | SAFETY, CLOUD_HOLD. Distinguish configured actions and device failure states. |
| site/flows.html:291 | Twenty-one offered blocks, table of every current output. | Stale | FLOW, MOSAIC. One type is legacy; outputs for panel passes and reports changed. Node-by-node decisions follow. |
| site/flows.html:331 | Compiler identifies every dropped setting; doctor all wiring/tier conflicts. | Qualify | CHECKS. It reports implemented checks; no exhaustiveness guarantee. |
| site/flows.html:376 | Single-target, seasonal pool, HFR-driven refocus patterns guarantee sleep/result. | Retain patterns only | FLOW_EXAMPLES, TARGET_POOL, FOCUS. Current examples define actual graphs; remove count and guaranteed outcome. |
| site/flows.html:412 | Same sequence engine means canvas cannot grow its own bugs. | False | FLOW. Shared engine is true; compiler/editor can have independent defects. Cut the inference. |

### Every row in the old node catalogue

All rows below originate at `site/flows.html:300` through `site/flows.html:320`, in the order listed. Their authoritative definitions are `server/astrodeck/flows/nodes.py:181` through `:468`.

| Baseline source | Node | Finding | Decision and evidence |
|---|---|---|---|
| site/flows.html:300 | DUSK WINDOW | Retain with configuration | FLOW, DUSK: repeat scheduling exists; cloud forecast no longer vetoes. |
| site/flows.html:301 | TARGET | Stale | MOSAIC: current block owns panels, centring and angle behavior, not just object coordinates. |
| site/flows.html:302 | SAFETY MONITOR | Retain bounded | SAFETY: configured sensor/stale policy; hardware/configuration matters. |
| site/flows.html:303 | CLOUD WATCH | Retain bounded | FRAME_CLOUD, CLOUD_HOLD: frame-derived debounced verdict; no every-source assumption. |
| site/flows.html:304 | DOME CONTROL | Qualify | SAFETY: connected dome and closure settings required. |
| site/flows.html:305 | FLAT PANEL | Retain bounded | CALIBRATION: compatible panel/cover required. |
| site/flows.html:306 | SLEW + CENTER | Stale | MOSAIC: legacy-only type at flows/nodes.py:468, centring now belongs to TARGET. |
| site/flows.html:307 | AUTOFOCUS | Qualify | FOCUS, PROVIDERS: native wheel/backend provider required; current pass output omitted by old table. |
| site/flows.html:308 | GUIDE | Qualify | GUIDE: provider-dependent; current pass output omitted by old table. |
| site/flows.html:309 | CAPTURE LOOP | Stale output description | FLOW, MOSAIC: now has all-done/frame/pass structure; no longer describe as only fixed one-target complete. |
| site/flows.html:310 | FILTER CYCLE | Retain with current outputs | FILTER_CYCLE, MOSAIC: interleaved capture and panel passes. |
| site/flows.html:311 | DUSK FLATS | Retain bounded | CALIBRATION: compatible configuration required; numerical sun window omitted from marketing. |
| site/flows.html:312 | CALIBRATION QUEUE | Retain bounded | CALIBRATION; flows/nodes.py:344 explains optional ports and missing-panel behavior. |
| site/flows.html:313 | TARGET POOL | Retain bounded | TARGET_POOL: candidate availability and quota logic, no guaranteed season completion. |
| site/flows.html:314 | CONDITION | Retain | FLOW; flows/nodes.py:393. |
| site/flows.html:315 | HOLD / RESUME | Qualify | CLOUD_HOLD: recovery prerequisites and configured timeout. |
| site/flows.html:316 | NOTIFY | Retain | ALERTS; configured sink needed. |
| site/flows.html:317 | REFOCUS | Retain | FOCUS; frame-boundary scheduling, provider prerequisite. |
| site/flows.html:318 | PARK + CLOSE | Qualify | SAFETY; roof behavior is config, not dictated by node alone (nodes.py:432). |
| site/flows.html:319 | ABORT + PARK | Retain bounded | SAFETY; request can fail with device fault, no universal physical guarantee. |
| site/flows.html:320 | SESSION REPORT | Stale output description | REPORT; current target-done event at nodes.py:455, old table says no output. |

## Existing weather page

| Baseline source | Claim group | Finding | Decision and evidence |
|---|---|---|---|
| site/weather.html:7 | Satellite model answers whether cloud is between scope and target. | Retain bounded | CLOUD. Estimate with timing/coverage limitations. |
| site/weather.html:42 | See useful half of sky and choose target that stays clear. | Qualify | CLOUD. Keep planning outcome, no promised clear target. |
| site/weather.html:57 | Cloud overhead does not matter; diagram geometry exact for every sky. | Overbroad | CLOUD. Line-of-sight geometry matters but overhead cloud can affect a target. Remove universal heading and unsupported diagram certainty. |
| site/weather.html:137 | Quantitative schematic example implies real geometry/height precision. | Replace | CLOUD. No numeric performance claim in new copy; no private or mount-derived values. |
| site/weather.html:150 | Monitor dome and pointer, any patch exact number, ten-minute motion prediction. | Qualify | CLOUD. Estimated short-term drift with observation age; no exact forecast horizon guarantee. |
| site/weather.html:212 | Unknown height/wind mismatch and out-of-footprint explicitly shown. | Retain bounded | CLOUD_COVERAGE. Missing/stale/coverage states retained. |
| site/weather.html:225 | Forecast/satellite/frames automatically check each other. | Qualify | WEATHER, CLOUD, FRAME_CLOUD. Compare the sources; no automated validation inference. |
| site/weather.html:236 | Both forecast providers offer layered cloud, seeing/transparency/dewpoint/wind and prevent overcast unpark. | False/overbroad | WEATHER fields differ; Astrospheric gated. DUSK current rain veto, cloud advisory. |
| site/weather.html:246 | GOES18/19 public archive, refresh every five minutes, exact matching dome. | Qualify | CLOUD. Observation cadence and configured poll cadence differ; projection is estimate. |
| site/weather.html:256 | Frame bright-star/contrast reading cannot be wrong and stops run. | False | FRAME_CLOUD explicitly documents exposure/focus/calibration caveats; cloud verdict usually holds, not universal abort. |
| site/weather.html:264 | Satellite never operates closure; own frames decide exposure value. | Retain bounded | CLOUD_ADVISORY, SAFETY. Distinguish advisory service, frame hold and safety monitor. |
| site/weather.html:287 | Cloud capture hold always tracks, parks guider, optional hold darks; exact automatic recovery. | Qualify | CLOUD_HOLD. Conditional stop/park/watch/recovery paths and available hardware/providers. |
| site/weather.html:292 | Maximum hold allows indefinite wait or park/close. | Retain configured behavior | CLOUD_HOLD. Set timeout behavior, avoid treating it as an unconditional safety response. |
| site/weather.html:297 | Bad/cloudy forecast at dusk vetoes automatic unpark; ignore-weather override. | Stale/false | DUSK. Only positive rain forecast is veto; current UI names forecast rain. |
| site/weather.html:302 | Unsafe or stale safety state aborts/parks/closes/warms; manual resume. | Qualify | SAFETY. Configured devices/actions, no guarantee of physical completion or all safety recovery paths. |
| site/weather.html:307 | Every rejected frame re-shot and never lost from count. | Qualify | SESSION. Accepted goals and rejection bounds; no unbounded retry/retention guarantee. |
| site/weather.html:323 | Entire Americas covered, all other continents uncovered; all other weather tools global. | Qualify | CLOUD_COVERAGE and WEATHER. Satellite footprint and provider availability control actual reach. |
| site/weather.html:347 | NOAA/GOES attribution/source data relationship. | Retain relevant source credit | CLOUD. No implication that NOAA endorses AstroDeck. |

## New pages and newly surfaced information

The seven-page map is overview, features, Flows, hardware, weather, getting started and releases. There was no dedicated setup or release page in the baseline.

| New content | Evidence | What is added |
|---|---|---|
| Windows executable | WINDOWS, LAN, RELEASE | Exact asset name, normal-user execution, localhost first run, external data directory, NTFS/ReFS configuration, admin creation before LAN bind. No installer claim. |
| Windows source setup | SOURCE, PROVIDERS, ASTAP | Python requirement, venv and editable server install, UI build, COM/cloudmap extras, separate native engine and ASTAP requirements. Commands are traced, not advertised as freshly installed/tested. |
| Orange Pi path | ORANGE, RELEASE | 64-bit Linux executable is distinct from the commissioned appliance flow. Unique printed board password, authorized 15-minute hotspot and portal are documented. No ready-to-flash image or product-shipping claim. |
| NINA setup | NINA | Advanced API plugin, equipment connection in NINA, configured host/default port, role assignment. |
| ASIAIR scope | ASIAIR | Equal homepage planning route, box retains capture/guiding. Experimental optional protocol adapter is described separately with only supported roles and fake-test limitation. |
| Current Flows | MOSAIC, FILTER_CYCLE, SESSION | TARGET-owned panel framing, panel passes, accepted-frame progress and multi-night continuation. |
| Release entries | RELEASE_HISTORY | 0.3.36 through 0.3.39 summaries come from release/deployment records, not guessed from the source version or external latest-release state. |
| Release prerequisites | RELEASE, PROVIDERS, ASTAP, OFFLINE, UPDATE | Actual asset list is checked at GitHub Releases; target matrix is not proof of a published asset. Distinguish tarball signatures from OS code signing. |

## Recorded hardware evidence and remaining limits

- `docs/hardware/zwo-am5-lx200-protocol.md:165` records native AM5N validation, including motion/tracking. It does not independently verify every AM5-family model.
- `docs/hardware/native-cameras-validation.md:62` through `:83` records real ASI220MM/Poseidon SDK layouts, ranges and Poseidon read-mode behavior. The table says exactly that. A planned phase in the same runbook is not treated as a completed test.
- `docs/hardware/rotator-focuser-filterwheel-native.md:8` records EAF/CAA device inventory. Identification is not a full native-operation or unattended-night test, so those rows remain Supported.
- `docs/hardware/rotator-focuser-filterwheel-native.md:178` records the Snowflake protocol checked on hardware.
- Native camera drivers enumerate SDK-listed devices; the code cannot establish successful testing of every returned model. The copy never uses an all-model Verified label.
- No rig was contacted and no new hardware certification was performed. Claude was informed of the missing/limited evidence rather than silently upgrading it.

## Release-record audit

| Version | Local record | Public summary boundary |
|---|---|---|
| 0.3.39 | `scripts/deploy_0339.ps1:9`; release commit `8e139b3f` | Session recovery, guide/park handling, Flows/Monitor controls, report retry and dependency-floor deployment checks. No all-defects-fixed claim. |
| 0.3.38 | `scripts/deploy_0338.ps1:9`; release commit `85488502` | Lost mount links, sun checks, alerts, log/report privacy and first-mosaic findings. No blanket safety certification. |
| 0.3.37 | `scripts/deploy_0337.ps1:10`; release commit `30944bc4` | Rotation uses solve filter; stopping within group angle tolerance is accepted. |
| 0.3.36 | `scripts/deploy_0336.ps1:12`; release commit `fa2a05d4` | Target editor/canvas, run/Continue, phone readouts, Send to Flow Wizard doors. |

Local annotated tags in this checkout stop at 0.3.32. The new release summaries therefore use the later explicit release commits and deployment notes. They do not imply public download assets for each entry. No live release lookup or GitHub mutation was performed by this task.

## Screenshot and attribution contract

The parent/design worker supplied simulator screenshots. Flows is a mosaic editor in red night mode. The Monitor image follows one simulated exposure, has no sequence running and has weather monitoring off. JSON alt text and captions state those facts. The site must not present the idle image as an active unattended imaging run. No Atlas survey screenshot is requested or approved in this copy.

DSS2 attribution uses the application's existing wording: `DSS2 imagery (c) AAO/STScI, served from CDS/ESA HiPS mirrors.` Mentioning the optional tiles is not a claim they ship in releases. The new copy contains no real observing-site values and no mount-coordinate readings.

## Style and work scope

Read both house-style commits (`0249f03d`, `13d089b9`) and applied HumanWriting plus its natural-writing checklist. The copy uses direct operator tasks, ordinary verbs and qualified capability statements. No em/en dashes, emojis, test-count marketing, promises of perfect unattended operation, invented nightly verification or generic every-vendor support remain in the authored JSON.

This task created only `tools/site/content.json`, `tools/site/claims-ledger.md` and `tools/site/content-audit.md`. No README, docs, source behavior, real configuration or Git history was changed. The parent owns HTML integration, screenshots, final privacy scanning and website checks. All findings were sent to the parent as they were discovered; native packaging (#630) and stale DSS workflow (#631) were confirmed by Claude.
