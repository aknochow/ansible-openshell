# SPDX-License-Identifier: Apache-2.0

"""ssh_proxy must speak whichever SSH proto the installed SDK generated."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from openshell._proto import openshell_pb2


def _proxy():
    path = Path(__file__).resolve().parents[2] / "scripts" / "ssh_proxy.py"
    spec = importlib.util.spec_from_file_location("ssh_proxy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ssh_forward_messages_match_the_installed_proto():
    proxy = _proxy()
    session, init = proxy.ssh_forward_messages(openshell_pb2, "meek-grison", "id-1", "default")
    fields = openshell_pb2.CreateSshSessionRequest.DESCRIPTOR.fields_by_name
    if "workspace_scope" in fields:
        assert session.sandbox == "meek-grison"
        assert session.workspace_scope.workspace == "default"
        assert init.sandbox == "meek-grison"
        assert init.workspace == "default"
        assert init.service_id == "ssh-proxy:meek-grison"
    else:
        assert session.sandbox_id == "id-1"
        assert init.sandbox_id == "id-1"
        assert init.service_id == "ssh-proxy:id-1"
    assert init.HasField("ssh")
