"""The self-updater must carry forward the bundled SDK libraries a release
tarball may not ship (build_release omits Player One's, #199), gated on the
staged manifest. The 0.3.23 hand deploy did this by hand; the updater did not,
and the imaging camera would have vanished on the first automatic update."""
import hashlib
import json
import sys

import pytest

from astrodeck.update import stage as S

PO = b"player one bytes"
ZWO_NEW = b"NEW zwo bytes"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tree(tmp_path):
    """This install's vendor tree (src) and a staged release's (dst)."""
    src = tmp_path / "current" / "vendor"
    dst = tmp_path / "staged" / "vendor"
    (src / "playerone" / "linux-arm64").mkdir(parents=True)
    (src / "zwo").mkdir()
    (dst / "zwo").mkdir(parents=True)
    (src / "playerone" / "PlayerOneCamera.dll").write_bytes(PO)
    (src / "playerone" / "linux-arm64" / "libPlayerOneCamera.so.3.10.0").write_bytes(PO)
    (src / "playerone" / "README.md").write_text("docs, not a library")
    (src / "zwo" / "ASICamera2.dll").write_bytes(b"old zwo bytes")
    (dst / "zwo" / "ASICamera2.dll").write_bytes(ZWO_NEW)  # the tarball ships ZWO's
    manifest = {"schema": 1, "binaries": {
        "playerone/PlayerOneCamera.dll":
            {"sha256": _sha(PO), "size": len(PO), "vendor": "playerone"},
        "playerone/linux-arm64/libPlayerOneCamera.so.3.10.0":
            {"sha256": _sha(PO), "size": len(PO), "vendor": "playerone"},
        "zwo/ASICamera2.dll":
            {"sha256": _sha(ZWO_NEW), "size": len(ZWO_NEW), "vendor": "zwo"},
    }}
    (dst / "manifest.json").write_text(json.dumps(manifest))
    return src, dst


def test_carries_pinned_libraries_the_tarball_omits(tmp_path):
    src, dst = _tree(tmp_path)
    res = S.carry_forward_vendor_libraries(src, dst)
    assert sorted(res["carried"]) == [
        "playerone/PlayerOneCamera.dll",
        "playerone/linux-arm64/libPlayerOneCamera.so.3.10.0",
    ]
    assert res["skipped"] == [] and res["reason"] == ""
    assert (dst / "playerone" / "PlayerOneCamera.dll").read_bytes() == PO
    assert (dst / "playerone" / "linux-arm64"
            / "libPlayerOneCamera.so.3.10.0").read_bytes() == PO
    # the release's own copy is never overwritten by the older one on the rig
    assert (dst / "zwo" / "ASICamera2.dll").read_bytes() == ZWO_NEW
    # documentation is not a library
    assert not (dst / "playerone" / "README.md").exists()


def test_refuses_a_library_the_manifest_does_not_pin_or_that_differs(tmp_path):
    src, dst = _tree(tmp_path)
    (src / "playerone" / "PlayerOneCamera.dll").write_bytes(b"tampered")
    (src / "zwo" / "EAF_focuser.dll").write_bytes(b"stray")  # unpinned, unshipped
    res = S.carry_forward_vendor_libraries(src, dst)
    assert res["carried"] == ["playerone/linux-arm64/libPlayerOneCamera.so.3.10.0"]
    assert dict(res["skipped"]) == {
        "playerone/PlayerOneCamera.dll": "does not match the staged manifest",
        "zwo/EAF_focuser.dll": "not pinned by the staged manifest",
    }
    assert not (dst / "playerone" / "PlayerOneCamera.dll").exists()
    assert not (dst / "zwo" / "EAF_focuser.dll").exists()


@pytest.mark.skipif(sys.platform == "win32",
                    reason="symlink creation needs a privilege on Windows")
def test_never_follows_a_symlink_out_of_the_vendor_tree(tmp_path):
    src, dst = _tree(tmp_path)
    outside = tmp_path / "evil.dll"
    outside.write_bytes(PO)  # identical bytes: only the link check can refuse it
    link = src / "playerone" / "PlayerOneCamera.dll"
    link.unlink()
    link.symlink_to(outside)
    res = S.carry_forward_vendor_libraries(src, dst)
    assert "playerone/PlayerOneCamera.dll" not in res["carried"]
    assert not (dst / "playerone" / "PlayerOneCamera.dll").exists()


def test_tolerates_missing_manifest_or_source_tree(tmp_path):
    src, dst = _tree(tmp_path)
    (dst / "manifest.json").unlink()
    res = S.carry_forward_vendor_libraries(src, dst)
    assert res["carried"] == [] and res["reason"]
    # a checkout without a vendor tree at all (a bare dev install)
    res2 = S.carry_forward_vendor_libraries(tmp_path / "nowhere", dst)
    assert res2["carried"] == [] and res2["reason"]


def test_cli_reports_what_it_carried(tmp_path, capsys):
    src, dst = _tree(tmp_path)
    assert S._main(["carry-forward", str(src), str(dst)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "playerone/PlayerOneCamera.dll" in out["carried"]
    assert (dst / "playerone" / "PlayerOneCamera.dll").read_bytes() == PO
