# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


class ModuleDocFragment:

    DOCUMENTATION = r"""
options:
  gateway:
    description:
      - URL of the OpenShell gateway.
      - If the value is not specified, the value of the E(OPENSHELL_GATEWAY_URL) environment variable will be used.
    type: str
    required: true
  tls_cert:
    description:
      - Path to a client TLS certificate for mTLS authentication.
      - Must be used together with O(tls_key).
      - If the value is not specified, the value of the E(OPENSHELL_TLS_CERT) environment variable will be used.
    type: path
  tls_key:
    description:
      - Path to the client TLS private key for mTLS authentication.
      - Must be used together with O(tls_cert).
      - If the value is not specified, the value of the E(OPENSHELL_TLS_KEY) environment variable will be used.
    type: path
  tls_ca:
    description:
      - Path to a custom CA certificate bundle for verifying the gateway's TLS certificate.
      - If the value is not specified, the value of the E(OPENSHELL_TLS_CA) environment variable will be used.
    type: path
  bearer_token:
    description:
      - OIDC bearer token for authentication.
      - If the value is not specified, the value of the E(OPENSHELL_BEARER_TOKEN) environment variable will be used.
    type: str
  timeout:
    description:
      - gRPC call timeout in seconds.
      - If the value is not specified, the value of the E(OPENSHELL_TIMEOUT) environment variable will be used.
    type: float
    default: 30.0
  workspace:
    description:
      - Workspace to scope sandbox operations to.
      - Gateways that don't implement workspace support yet accept and
        round-trip an empty string, so the default is safe even against
        those.
      - If the value is not specified, the value of the E(OPENSHELL_WORKSPACE) environment variable will be used.
    type: str
    default: ""
"""
