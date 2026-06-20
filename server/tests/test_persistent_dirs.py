"""ASTRODECK_CONFIG_DIR / ASTRODECK_CAPTURE_DIR relocate persistent state out of the
versioned release dir, so a self-update (which swaps the release dir) never wipes
config / profiles / plans / captured images. Tested in a clean subprocess because
the dirs are module-level constants resolved at import."""
import os
import subprocess
import sys


def test_env_overrides_relocate_persistent_dirs(tmp_path):
    cfg = tmp_path / "cfg"
    cap = tmp_path / "caps"
    env = dict(os.environ, ASTRODECK_CONFIG_DIR=str(cfg), ASTRODECK_CAPTURE_DIR=str(cap))
    code = (
        "import astrodeck.config as c, astrodeck.hub as h;"
        "print(c.CONFIG_DIR);print(c.CONFIG_FILE);print(c.PROFILES_DIR);"
        "print(c.PLANS_DIR);print(h.CAPTURE_DIR)"
    )
    out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
    lines = [l.strip() for l in out.strip().splitlines()]
    assert lines[0] == str(cfg)
    assert lines[1] == str(cfg / "astrodeck.json")
    assert lines[2] == str(cfg / "profiles")
    assert lines[3] == str(cfg / "plans")
    assert lines[4] == str(cap)


def test_defaults_when_env_unset():
    env = {k: v for k, v in os.environ.items()
           if k not in ("ASTRODECK_CONFIG_DIR", "ASTRODECK_CAPTURE_DIR")}
    code = ("import astrodeck.config as c;"
            "print(str(c.CONFIG_DIR).replace(chr(92),'/').endswith('server/config'))")
    out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
    assert out.strip() == "True"
