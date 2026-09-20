"""Controller-owned, same-night Guided checkpoints.

These are deliberately not configuration. A controller restart, a new observing
night, or changed equipment/site clears the checks; saved sites and horizons
remain in their existing stores. Browsers may report a human review, but only
current engine measurements can certify autofocus or polar alignment.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


class GuidedCheckpoint:
    def __init__(self):
        self.boot_id = secrets.token_hex(16)
        self._secret = secrets.token_bytes(32)
        self.context = ""
        self._identity = None
        self.revision = 0
        self.facts = dict.fromkeys(("location", "horizon", "focus", "alignment"), False)
        self.focus_proof = None
        self._focus_after = time.time()
        self._alignment_after = time.time()
        self.alignment_proof = None
        self.reason = "Review this setup before your first image."

    def invalidate(self, fact: str, reason: str = "The setup changed. Review the remaining checks.", *, proof_boundary: bool = True):
        keys = list(self.facts)
        for key in keys[keys.index(fact):]:
            self.facts[key] = False
        if fact != "alignment":
            self.focus_proof = None
        if fact != "alignment" and proof_boundary:
            self._focus_after = time.time()
        self.alignment_proof = None
        self._alignment_after = time.time()
        self.reason = reason
        self.revision += 1

    def observe(self, identity: dict, focus: dict | None, polar: dict, *, focuser_position=None):
        # HMAC prevents this opaque comparison key revealing a low-entropy site
        # position to a caller who cannot read precise coordinates.
        context = hmac.new(self._secret, encoded(identity), hashlib.sha256).hexdigest()
        if context != self.context:
            old = self._identity
            fact = "location" if old is None or any(old.get(k) != identity.get(k) for k in ("night", "site", "devices", "links", "profile", "mode")) else "horizon" if old.get("horizon") != identity.get("horizon") else "focus"
            self.invalidate(fact, "The controller, observing night, site, or equipment changed. Review the remaining setup checks.")
            self._focus_after = time.time()
            self._identity = json.loads(encoded(identity))
            self.context = context
        if self.facts["focus"] and (encoded(focus) != self.focus_proof or
                not self.focus_valid(focus, focuser_position)):
            # A new engine result may have completed between HTTP polls. It is
            # new evidence, not a reason to demand a still newer autofocus.
            # A changed physical position with the same result is different.
            self.invalidate("focus", "Focus changed. Check the stars before continuing.",
                            proof_boundary=encoded(focus) == self.focus_proof)
        if self.facts["alignment"] and (polar.get("reading_ts") != self.alignment_proof or
                polar.get("state") not in ("idle", "done")):
            self.invalidate("alignment", "Alignment changed. Finish and check the current measurement.")

    def focus_valid(self, focus, position):
        best = (focus or {}).get("best") or {}
        hfr = best.get("hfr")
        return (focus or {}).get("state") == "done" and (focus or {}).get("_observed_at", 0) >= self._focus_after and isinstance(hfr, (int, float)) and math.isfinite(hfr) and hfr > 0 and position is not None and position == best.get("position")

    def complete(self, fact: str, focus: dict | None, polar: dict, *, focuser_position=None,
                 mount_slewing=False, simulated=False, now=None):
        if fact == "horizon" and not self.facts["location"]:
            raise ValueError("Review the observing location first.")
        if fact in ("focus", "alignment") and not self.facts["horizon"]:
            raise ValueError("Review the horizon first.")
        if fact == "focus":
            if not self.focus_valid(focus, focuser_position):
                raise ValueError("A completed autofocus measurement at the current focuser position is needed to recover this check on another device. Manual focus remains available in this browser.")
            self.focus_proof = encoded(focus)
        if fact == "alignment":
            age = (time.time() if now is None else now) - (polar.get("reading_ts") or 0)
            error = polar.get("total_error")
            if (polar.get("state") not in ("idle", "done") or polar.get("phase") == "measuring" or
                    not isinstance(error, (int, float)) or not math.isfinite(error) or not 0 <= error <= 2 or
                    not 0 <= age < 300 or (polar.get("reading_ts") or 0) < self._alignment_after or mount_slewing or polar.get("stale_updates") or polar.get("flags") or
                    (polar.get("source") == "sim" and not simulated)):
                raise ValueError("Finish alignment with a fresh, clear measurement within two arcminutes first.")
            self.alignment_proof = polar["reading_ts"]
        self.facts[fact] = True
        self.reason = "Setup checks recovered from this controller."
        self.revision += 1

    def snapshot(self):
        return {"boot_id": self.boot_id, "context": self.context, "revision": self.revision,
                "facts": dict(self.facts), "reason": self.reason}
