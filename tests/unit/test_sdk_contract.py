# SPDX-License-Identifier: Apache-2.0

"""Lock the NVIDIA openshell SDK surface this collection actually calls.

These tests import the real installed SDK (no mocks). They exist so a
future lockfile bump fails here instead of at a live gateway with
TypeError: missing 1 required keyword-only argument: 'workspace'.
"""

from __future__ import annotations

import inspect
from importlib.metadata import version

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    OPENSHELL_SDK_SPEC,
)
from openshell import SandboxClient
from openshell._proto import datamodel_pb2, openshell_pb2, sandbox_pb2
from packaging.version import Version


def test_create_get_delete_list_wait_require_workspace_kwarg():
    for method_name in ("create", "get", "delete", "list", "wait_ready", "wait_deleted"):
        params = inspect.signature(getattr(SandboxClient, method_name)).parameters
        assert "workspace" in params, method_name
        assert params["workspace"].kind is inspect.Parameter.KEYWORD_ONLY, method_name


def test_exec_and_health_signatures():
    exec_params = inspect.signature(SandboxClient.exec).parameters
    # exec is id-scoped, not workspace-scoped, matching sandbox_exec.py.
    assert list(exec_params)[1] == "sandbox_id"
    assert "workspace" not in exec_params
    assert list(inspect.signature(SandboxClient.health).parameters) == ["self"]


def test_sandbox_phase_enum_includes_start_stop():
    assert openshell_pb2.SANDBOX_PHASE_READY == 2
    assert openshell_pb2.SANDBOX_PHASE_STOPPING == 6
    assert openshell_pb2.SANDBOX_PHASE_STOPPED == 7
    assert openshell_pb2.SANDBOX_PHASE_STARTING == 8


def test_proto_types_used_by_modules_still_exist():
    assert hasattr(openshell_pb2, "SandboxSpec")
    assert hasattr(openshell_pb2, "SandboxTemplate")
    assert hasattr(openshell_pb2, "CreateSshSessionRequest")
    assert hasattr(openshell_pb2, "TcpForwardFrame")
    assert hasattr(sandbox_pb2, "SandboxPolicy")
    assert hasattr(sandbox_pb2, "FilesystemPolicy")
    assert hasattr(datamodel_pb2, "Provider")
    assert hasattr(datamodel_pb2, "ObjectMeta")


def test_collection_pin_matches_installed_sdk():
    installed = Version(version("openshell"))
    assert OPENSHELL_SDK_SPEC == "openshell>=0.0.116,<0.0.120"
    assert installed >= Version("0.0.116")
    assert installed < Version("0.0.120")
