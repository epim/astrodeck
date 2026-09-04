"""Host-safe fixtures for the standalone Orange Pi provisioner."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest


PROVISION_DIR = Path(__file__).parents[1] / "provision"
PROVISIONER = PROVISION_DIR / "astrodeck-provision.py"
BROKER = PROVISION_DIR / "astrodeck-provision-broker.py"


def _load(path: Path, prefix: str):
    name = f"{prefix}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name, module


@pytest.fixture
def prov():
    name, module = _load(PROVISIONER, "astrodeck_provision_test")
    try:
        module.DONE.clear()
        module.SERVER_FAILED.clear()
        module.STATUS.update(phase="portal", error="", ssid="")
        module.SCAN_CACHE = []
        yield module
    finally:
        module.DONE.set()
        sys.modules.pop(name, None)


@pytest.fixture
def broker():
    name, module = _load(BROKER, "astrodeck_provision_broker_test")
    try:
        module.AP_MAY_BE_ACTIVE.clear()
        module.SCAN_CACHE = []
        yield module
    finally:
        module.AP_MAY_BE_ACTIVE.clear()
        sys.modules.pop(name, None)
