# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import grpc
import pytest


@pytest.fixture(autouse=True)
def mock_openshell():
    """Mock the openshell SDK, same shape as test_sandbox.py's fixture."""
    mock_sdk = MagicMock()
    mock_sdk.TlsConfig = MagicMock()
    mock_sdk.SandboxClient = MagicMock()
    sys.modules["openshell"] = mock_sdk
    sys.modules["openshell._proto"] = MagicMock()
    sys.modules["openshell._proto.datamodel_pb2"] = MagicMock()
    sys.modules["openshell._proto.openshell_pb2"] = MagicMock()
    sys.modules["openshell._proto.openshell_pb2_grpc"] = MagicMock()
    yield mock_sdk
    for mod in [
        "openshell",
        "openshell._proto",
        "openshell._proto.datamodel_pb2",
        "openshell._proto.openshell_pb2",
        "openshell._proto.openshell_pb2_grpc",
    ]:
        sys.modules.pop(mod, None)


class FakeRpcError(grpc.RpcError):
    """A real grpc.RpcError subclass with a controllable .code() -- real
    gRPC errors are raised by C-extension internals we can't construct
    directly in a test, but `except grpc.RpcError` only cares that the
    raised object IS a grpc.RpcError with a .code() method."""

    def __init__(self, code):
        super().__init__()
        self._code = code

    def code(self):
        return self._code


def make_provider_response(type_, credentials, config):
    resp = MagicMock()
    resp.provider.type = type_
    resp.provider.credentials = credentials
    resp.provider.config = config
    resp.provider.metadata.name = "test-provider"
    return resp


def _run_provider_main(module_params):
    """Call provider.main() with AnsibleModule/get_client mocked out,
    returning (fake_module, stub) so tests can assert on both."""
    from ansible_collections.aknochow.openshell.plugins.modules import provider as provider_module

    fake_module = MagicMock()
    fake_module.params = module_params
    fake_client = MagicMock()
    stub = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value

    with patch.object(provider_module, "AnsibleModule", lambda **kw: fake_module), patch.object(
        provider_module, "get_client", lambda module: fake_client
    ):
        provider_module.main()

    return fake_module, stub


PRESENT_PARAMS = dict(
    name="test-provider",
    type="claude",
    credentials={"api_key": "new-key"},
    config={"region": "us-east5"},
    state="present",
)

ABSENT_PARAMS = dict(
    name="test-provider",
    type=None,
    credentials={},
    config={},
    state="absent",
)


class TestProviderPresent:
    def test_requires_type(self):
        fake_module, stub = _run_provider_main(dict(PRESENT_PARAMS, type=None))

        fake_module.fail_json.assert_called_once()
        assert "type" in fake_module.fail_json.call_args.kwargs["msg"]
        stub.CreateProvider.assert_not_called()

    def test_creates_when_not_found(self):
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.side_effect = FakeRpcError(grpc.StatusCode.NOT_FOUND)
        stub_ref.CreateProvider.return_value = make_provider_response(
            "claude", {"api_key": "new-key"}, {"region": "us-east5"}
        )

        fake_module, stub = _run_provider_main(PRESENT_PARAMS)

        stub.CreateProvider.assert_called_once()
        fake_module.exit_json.assert_called_once()
        assert fake_module.exit_json.call_args.kwargs["changed"] is True

    def test_lookup_permission_denied_fails_loudly_instead_of_creating(self):
        # Regression check: a bare `except Exception` previously treated
        # ANY GetProvider failure -- including PERMISSION_DENIED -- as
        # "doesn't exist yet" and silently attempted CreateProvider
        # instead. Fails against the pre-fix code (CreateProvider gets
        # called); passes once only NOT_FOUND is treated that way.
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.side_effect = FakeRpcError(grpc.StatusCode.PERMISSION_DENIED)

        fake_module, stub = _run_provider_main(PRESENT_PARAMS)

        stub.CreateProvider.assert_not_called()
        stub.UpdateProvider.assert_not_called()
        fake_module.fail_json.assert_called_once()
        assert "test-provider" in fake_module.fail_json.call_args.kwargs["msg"]

    def test_no_op_when_desired_state_already_matches(self):
        # Regression check: reporting changed=True unconditionally broke
        # idempotency -- a second run with identical inputs should report
        # changed=False and skip UpdateProvider entirely.
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.return_value = make_provider_response(
            "claude", {"api_key": "new-key"}, {"region": "us-east5"}
        )

        fake_module, stub = _run_provider_main(PRESENT_PARAMS)

        stub.UpdateProvider.assert_not_called()
        stub.CreateProvider.assert_not_called()
        fake_module.exit_json.assert_called_once()
        assert fake_module.exit_json.call_args.kwargs["changed"] is False

    def test_updates_when_desired_state_differs(self):
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.side_effect = [
            make_provider_response("claude", {"api_key": "old-key"}, {"region": "us-west1"}),
            make_provider_response("claude", {"api_key": "new-key"}, {"region": "us-east5"}),
        ]

        fake_module, stub = _run_provider_main(PRESENT_PARAMS)

        stub.UpdateProvider.assert_called_once()
        fake_module.exit_json.assert_called_once()
        assert fake_module.exit_json.call_args.kwargs["changed"] is True

    def test_update_permission_denied_fails_loudly(self):
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.return_value = make_provider_response(
            "claude", {"api_key": "old-key"}, {"region": "us-west1"}
        )
        stub_ref.UpdateProvider.side_effect = FakeRpcError(grpc.StatusCode.PERMISSION_DENIED)

        fake_module, stub = _run_provider_main(PRESENT_PARAMS)

        fake_module.fail_json.assert_called_once()
        assert "update" in fake_module.fail_json.call_args.kwargs["msg"].lower()


class TestProviderAbsent:
    def test_noop_when_not_found(self):
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.side_effect = FakeRpcError(grpc.StatusCode.NOT_FOUND)

        fake_module, stub = _run_provider_main(ABSENT_PARAMS)

        stub.DeleteProvider.assert_not_called()
        fake_module.exit_json.assert_called_once_with(changed=False)

    def test_lookup_permission_denied_fails_loudly_instead_of_reporting_deleted(self):
        # Regression check: a bare `except Exception` previously treated
        # ANY GetProvider failure as "already deleted" and reported a
        # successful no-op. Fails against the pre-fix code
        # (exit_json(changed=False) gets called); passes once only
        # NOT_FOUND is treated that way.
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.side_effect = FakeRpcError(grpc.StatusCode.PERMISSION_DENIED)

        fake_module, stub = _run_provider_main(ABSENT_PARAMS)

        stub.DeleteProvider.assert_not_called()
        fake_module.exit_json.assert_not_called()
        fake_module.fail_json.assert_called_once()
        assert "test-provider" in fake_module.fail_json.call_args.kwargs["msg"]

    def test_deletes_when_found(self):
        stub_ref = sys.modules["openshell._proto"].openshell_pb2_grpc.OpenShellStub.return_value
        stub_ref.GetProvider.return_value = make_provider_response("claude", {}, {})

        fake_module, stub = _run_provider_main(ABSENT_PARAMS)

        stub.DeleteProvider.assert_called_once()
        fake_module.exit_json.assert_called_once_with(changed=True)
