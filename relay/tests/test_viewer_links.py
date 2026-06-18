"""Viewer-link registry: jti revocation, max-viewers, renew, audit (W3.3.5)."""
from __future__ import annotations

from relay.principal import PrincipalSigner, ViewerLinkSpec
from relay.viewer_links import ViewerLinkRegistry


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _registry(clock):
    audit = []
    reg = ViewerLinkRegistry(PrincipalSigner.dev_hmac(b"viewer-key"),
                             audit=lambda e, f: audit.append((e, f)),
                             clock=clock)
    return reg, audit


def test_issue_records_audit():
    clk = FakeClock()
    reg, audit = _registry(clk)
    token, jti = reg.issue(ViewerLinkSpec(label="friend", ttl_s=3600))
    assert token and jti
    assert audit[0][0] == "viewer_link.issue"
    assert audit[0][1]["jti"] == jti


def test_revoke_then_admit_denied():
    clk = FakeClock()
    reg, _ = _registry(clk)
    _, jti = reg.issue(ViewerLinkSpec())
    assert reg.admit(jti, "wsA") is True
    assert reg.revoke(jti) is True
    assert reg.is_revoked(jti) is True
    assert reg.admit(jti, "wsB") is False


def test_double_revoke_is_noop():
    clk = FakeClock()
    reg, _ = _registry(clk)
    _, jti = reg.issue(ViewerLinkSpec())
    assert reg.revoke(jti) is True
    assert reg.revoke(jti) is False


def test_expired_link_is_revoked():
    clk = FakeClock(0.0)
    reg, _ = _registry(clk)
    _, jti = reg.issue(ViewerLinkSpec(ttl_s=100))
    assert reg.is_revoked(jti) is False
    clk.t = 101.0
    assert reg.is_revoked(jti) is True


def test_max_viewers_enforced():
    clk = FakeClock()
    reg, _ = _registry(clk)
    _, jti = reg.issue(ViewerLinkSpec(max_viewers=2))
    assert reg.admit(jti, "a") is True
    assert reg.admit(jti, "b") is True
    assert reg.admit(jti, "c") is False     # over the cap
    reg.release(jti, "a")
    assert reg.admit(jti, "c") is True       # slot freed


def test_renew_extends_exp():
    clk = FakeClock(0.0)
    reg, _ = _registry(clk)
    _, jti = reg.issue(ViewerLinkSpec(ttl_s=100, renew_while_connected=True))
    clk.t = 90.0
    assert reg.renew(jti) is True
    clk.t = 150.0
    # would have expired at 100 without the renew; renewed to 90+100=190.
    assert reg.is_revoked(jti) is False


def test_renew_forbidden_when_disabled():
    clk = FakeClock(0.0)
    reg, _ = _registry(clk)
    _, jti = reg.issue(ViewerLinkSpec(ttl_s=100, renew_while_connected=False))
    assert reg.renew(jti) is False


def test_unknown_jti_is_revoked_and_unadmittable():
    clk = FakeClock()
    reg, _ = _registry(clk)
    assert reg.is_revoked("never-issued") is True
    assert reg.admit("never-issued", "x") is False
