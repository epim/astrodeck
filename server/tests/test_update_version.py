"""Semver parse/compare/select for the self-update feature."""
from astrodeck.update import version as V


def test_parse_basic_and_prefixed():
    assert str(V.parse("0.2.0")) == "0.2.0"
    assert str(V.parse("v0.2.0")) == "0.2.0"
    assert V.parse("v1.2.3-rc1").pre == ("rc1",)
    assert V.parse("1.2.3+build.7").pre == ()       # build metadata ignored
    assert V.parse("not-a-version") is None
    assert V.parse("1.2") is None
    assert V.parse(None) is None


def test_release_outranks_prerelease():
    assert V.is_newer("0.2.0", "0.2.0-rc1")
    assert not V.is_newer("0.2.0-rc1", "0.2.0")


def test_ordering():
    assert V.is_newer("0.2.0", "0.1.9")
    assert V.is_newer("1.0.0", "0.9.9")
    assert V.is_newer("0.2.1", "0.2.0")
    assert not V.is_newer("0.2.0", "0.2.0")          # equal is not newer
    # dot-separated numeric identifiers compare numerically, not lexically
    assert V.is_newer("0.2.0-rc.10", "0.2.0-rc.2")
    # more pre-release fields outrank a prefix
    assert V.is_newer("0.2.0-rc.1", "0.2.0-rc")


def test_is_newer_failsafe_on_garbage():
    assert not V.is_newer("garbage", "0.1.0")
    assert not V.is_newer("0.1.0", "garbage")


def test_select_latest_stable_ignores_prereleases():
    tags = ["v0.1.0", "v0.2.0", "v0.3.0-rc1"]
    assert str(V.select_latest(tags, channel="stable")) == "0.2.0"
    assert str(V.select_latest(tags, channel="prerelease")) == "0.3.0-rc1"


def test_select_latest_requires_strictly_newer_than_current():
    tags = ["v0.1.0", "v0.2.0"]
    assert str(V.select_latest(tags, channel="stable", current="0.1.0")) == "0.2.0"
    assert V.select_latest(tags, channel="stable", current="0.2.0") is None
    assert V.select_latest(tags, channel="stable", current="0.9.0") is None


def test_select_latest_empty_and_unparseable():
    assert V.select_latest([], channel="stable") is None
    assert V.select_latest(["nope", "also-bad"], channel="stable") is None
