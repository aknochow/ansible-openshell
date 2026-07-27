# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_openshell():
    """Mock the openshell SDK so tests run without it installed."""
    mock_sdk = MagicMock()
    mock_sdk.TlsConfig = MagicMock()
    mock_sdk.SandboxClient = MagicMock()
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


class TestGatewayArgspec:
    def test_argspec_has_required_fields(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            GATEWAY_ARGSPEC,
        )

        assert "gateway" in GATEWAY_ARGSPEC
        assert GATEWAY_ARGSPEC["gateway"]["required"] is True
        assert "tls_cert" in GATEWAY_ARGSPEC
        assert "tls_key" in GATEWAY_ARGSPEC
        assert "tls_ca" in GATEWAY_ARGSPEC
        assert "bearer_token" in GATEWAY_ARGSPEC
        assert "timeout" in GATEWAY_ARGSPEC

    def test_tls_key_is_no_log(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            GATEWAY_ARGSPEC,
        )

        assert GATEWAY_ARGSPEC["tls_key"]["no_log"] is True
        assert GATEWAY_ARGSPEC["bearer_token"]["no_log"] is True

    def test_timeout_default(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            GATEWAY_ARGSPEC,
        )

        assert GATEWAY_ARGSPEC["timeout"]["default"] == 30.0


class TestGetClient:
    def test_parses_https_url(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            get_client,
        )

        module = MagicMock()
        module.params = {
            "gateway": "https://openshell.apps.example.com",
            "tls_cert": None,
            "tls_key": None,
            "tls_ca": None,
            "bearer_token": None,
            "timeout": 30.0,
        }

        get_client(module)
        mock_openshell.SandboxClient.assert_called_once()
        call_kwargs = mock_openshell.SandboxClient.call_args
        assert call_kwargs.kwargs["endpoint"] == "openshell.apps.example.com:443"

    def test_parses_url_with_port(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            get_client,
        )

        module = MagicMock()
        module.params = {
            "gateway": "https://gateway.example.com:8443",
            "tls_cert": None,
            "tls_key": None,
            "tls_ca": None,
            "bearer_token": None,
            "timeout": 30.0,
        }

        get_client(module)
        call_kwargs = mock_openshell.SandboxClient.call_args
        assert call_kwargs.kwargs["endpoint"] == "gateway.example.com:8443"

    def test_passes_bearer_token(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            get_client,
        )

        module = MagicMock()
        module.params = {
            "gateway": "https://gw.example.com",
            "tls_cert": None,
            "tls_key": None,
            "tls_ca": None,
            "bearer_token": "my-token",
            "timeout": 30.0,
        }

        get_client(module)
        call_kwargs = mock_openshell.SandboxClient.call_args
        assert call_kwargs.kwargs["bearer_token"] == "my-token"

    def test_passes_timeout(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
            get_client,
        )

        module = MagicMock()
        module.params = {
            "gateway": "https://gw.example.com",
            "tls_cert": None,
            "tls_key": None,
            "tls_ca": None,
            "bearer_token": None,
            "timeout": 60.0,
        }

        get_client(module)
        call_kwargs = mock_openshell.SandboxClient.call_args
        assert call_kwargs.kwargs["timeout"] == 60.0


class TestTlsBuilder:
    def test_returns_none_when_no_tls(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.tls import (
            build_tls_config,
        )

        module = MagicMock()
        module.params = {"tls_cert": None, "tls_key": None, "tls_ca": None}

        result = build_tls_config(module)
        assert result is None

    def test_builds_mtls_config(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.tls import (
            build_tls_config,
        )

        module = MagicMock()
        module.params = {
            "tls_cert": "/path/tls.crt",
            "tls_key": "/path/tls.key",
            "tls_ca": "/path/ca.crt",
        }

        build_tls_config(module)
        mock_openshell.TlsConfig.assert_called_once()

    def test_fails_on_cert_without_key(self, mock_openshell):
        from ansible_collections.aknochow.openshell.plugins.module_utils.tls import (
            build_tls_config,
        )

        module = MagicMock()
        module.params = {
            "tls_cert": "/path/tls.crt",
            "tls_key": None,
            "tls_ca": None,
        }

        build_tls_config(module)
        module.fail_json.assert_called_once()
