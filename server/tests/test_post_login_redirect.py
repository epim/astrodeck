"""Post-login redirect lands back at the relay base (not the relay root) so a
tunnelled Google login returns to /h/<home>/ instead of a 404."""
from astrodeck.auth.routes import _post_login_path


class _Cfg:
    def __init__(self, redirect_uri):
        self.google_redirect_uri = redirect_uri


def test_relay_tunnel_redirect_returns_to_home_base():
    cfg = _Cfg("https://astrodeck-relay.fly.dev/h/home-1/auth/google/callback")
    assert _post_login_path(cfg) == "/h/home-1/"


def test_lan_redirect_returns_to_root():
    cfg = _Cfg("http://astrotown.lan:8800/auth/google/callback")
    assert _post_login_path(cfg) == "/"


def test_empty_or_unmatched_falls_back_to_root():
    assert _post_login_path(_Cfg("")) == "/"
    assert _post_login_path(_Cfg("https://x/other/path")) == "/"
