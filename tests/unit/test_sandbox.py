# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_openshell():
    """Mock the openshell SDK."""
    mock_sdk = MagicMock()
    mock_sdk.TlsConfig = MagicMock()
    mock_sdk.SandboxClient = MagicMock()
    mock_sdk.SandboxError = type("SandboxError", (RuntimeError,), {})
    sys.modules["openshell"] = mock_sdk
    sys.modules["openshell._proto"] = MagicMock()
    sys.modules["openshell._proto.openshell_pb2"] = MagicMock()
    sys.modules["openshell._proto.openshell_pb2_grpc"] = MagicMock()
    sys.modules["openshell._proto.inference_pb2"] = MagicMock()
    sys.modules["openshell._proto.inference_pb2_grpc"] = MagicMock()
    yield mock_sdk
    for mod in [
        "openshell",
        "openshell._proto",
        "openshell._proto.openshell_pb2",
        "openshell._proto.openshell_pb2_grpc",
        "openshell._proto.inference_pb2",
        "openshell._proto.inference_pb2_grpc",
    ]:
        sys.modules.pop(mod, None)


class TestSandboxModule:
    def test_phase_names_mapping(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            PHASE_NAMES,
        )

        assert PHASE_NAMES[0] == "UNSPECIFIED"
        assert PHASE_NAMES[2] == "READY"
        assert PHASE_NAMES[3] == "ERROR"

    def test_sandbox_to_dict(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            sandbox_to_dict,
        )

        ref = MagicMock()
        ref.id = "sandbox-123"
        ref.name = "test-sandbox"
        ref.workspace = "test-workspace"
        ref.status.phase = 2
        ref.status.current_policy_version = 1

        result = sandbox_to_dict(ref)
        assert result["id"] == "sandbox-123"
        assert result["name"] == "test-sandbox"
        assert result["workspace"] == "test-workspace"
        assert result["phase"] == "READY"
        assert result["policy_version"] == 1

    def test_create_requires_image(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.modules.sandbox import (
            create_sandbox,
        )

        module = MagicMock()
        module.params = {
            "image": None,
            "name": None,
            "environment": {},
            "wait": True,
            "wait_timeout": 300,
            "workspace": "",
        }
        client = MagicMock()

        create_sandbox(module, client)
        module.fail_json.assert_called_once()
        assert "image" in module.fail_json.call_args.kwargs["msg"]

    def test_delete_requires_name(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.modules.sandbox import (
            delete_sandbox,
        )

        module = MagicMock()
        module.params = {"name": None, "wait": True, "wait_timeout": 60, "workspace": ""}
        client = MagicMock()

        delete_sandbox(module, client)
        module.fail_json.assert_called_once()
        assert "name" in module.fail_json.call_args.kwargs["msg"]

    def test_delete_noop_when_not_found(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.modules.sandbox import (
            delete_sandbox,
        )

        module = MagicMock()
        module.params = {"name": "missing-sandbox", "wait": True, "wait_timeout": 60, "workspace": "my-workspace"}
        client = MagicMock()
        client.get.side_effect = mock_openshell.SandboxError("not found")

        delete_sandbox(module, client)
        module.exit_json.assert_called_once_with(changed=False)
        client.get.assert_called_once_with("missing-sandbox", workspace="my-workspace")


class TestSandboxExecModule:
    def test_exec_returns_result(self, mock_openshell):
        """Verify the exec module calls client.exec and exits with rc/stdout/stderr."""
        from ansible_collections.aknochow.openshell.plugins.modules.sandbox_exec import (
            main,
        )

        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "hello"
        mock_result.stderr = ""

        mock_sandbox_ref = MagicMock()
        mock_sandbox_ref.id = "sandbox-123"

        mock_client = MagicMock()
        mock_client.get.return_value = mock_sandbox_ref
        mock_client.exec.return_value = mock_result
        mock_openshell.SandboxClient.return_value = mock_client

        with patch(
            "ansible_collections.aknochow.openshell.plugins.modules.sandbox_exec.AnsibleModule"
        ) as mock_module_cls, patch(
            "ansible_collections.aknochow.openshell.plugins.modules.sandbox_exec.get_client"
        ) as mock_get_client:
            mock_module = MagicMock()
            mock_module.params = {
                "gateway": "https://gw.example.com",
                "tls_cert": None,
                "tls_key": None,
                "tls_ca": None,
                "bearer_token": None,
                "timeout": 30.0,
                "workspace": "",
                "sandbox": "test-sandbox",
                "command": ["echo", "hello"],
                "workdir": None,
                "environment": {},
                "stdin": None,
                "command_timeout": None,
            }
            mock_module_cls.return_value = mock_module
            mock_get_client.return_value = mock_client

            main()
            # Verifies the name-to-ID resolution added when workspace scoping
            # landed: exec() must receive the ID that get() resolved to
            # (sandbox-123), not the raw "test-sandbox" param — a bare
            # unconfigured mock_client.get() would let a MagicMock leak
            # through here instead, without failing the assertion below.
            mock_client.get.assert_called_once_with("test-sandbox", workspace="")
            mock_client.exec.assert_called_once_with(
                "sandbox-123",
                ["echo", "hello"],
                workdir=None,
                env={},
                stdin=None,
                timeout_seconds=None,
            )
            mock_module.exit_json.assert_called_once_with(
                changed=True, rc=0, stdout="hello", stderr=""
            )
