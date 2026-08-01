# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ansible.module_utils.basic import AnsibleModule, env_fallback

from .tls import build_tls_config

GATEWAY_ARGSPEC = dict(
    gateway=dict(
        type="str",
        required=True,
        fallback=(env_fallback, ["OPENSHELL_GATEWAY_URL"]),
    ),
    tls_cert=dict(
        type="path",
        fallback=(env_fallback, ["OPENSHELL_TLS_CERT"]),
    ),
    tls_key=dict(
        type="path",
        no_log=True,
        fallback=(env_fallback, ["OPENSHELL_TLS_KEY"]),
    ),
    tls_ca=dict(
        type="path",
        fallback=(env_fallback, ["OPENSHELL_TLS_CA"]),
    ),
    bearer_token=dict(
        type="str",
        no_log=True,
        fallback=(env_fallback, ["OPENSHELL_BEARER_TOKEN"]),
    ),
    timeout=dict(
        type="float",
        default=30.0,
        fallback=(env_fallback, ["OPENSHELL_TIMEOUT"]),
    ),
    # openshell>=0.0.88 scopes nearly every SandboxClient call to a
    # workspace (required keyword-only arg on create/get/delete/list/
    # wait_ready/wait_deleted/etc). Gateways that don't implement
    # workspace support yet (WorkspaceClient RPCs return UNIMPLEMENTED)
    # still accept and round-trip an empty string, so "" is a safe
    # default rather than guessing a "default" sentinel.
    workspace=dict(
        type="str",
        default="",
        fallback=(env_fallback, ["OPENSHELL_WORKSPACE"]),
    ),
)


PHASE_NAMES = {
    0: "UNSPECIFIED",
    1: "PROVISIONING",
    2: "READY",
    3: "ERROR",
    4: "DELETING",
    5: "UNKNOWN",
}


def sandbox_to_dict(ref: Any) -> dict:
    """Shared by sandbox.py and sandbox_info.py — same SandboxRef shape."""
    return dict(
        id=ref.id,
        name=ref.name,
        workspace=ref.workspace,
        phase=PHASE_NAMES.get(ref.status.phase, str(ref.status.phase)),
        policy_version=ref.status.current_policy_version,
    )


def get_workspace(module: AnsibleModule) -> str:
    """Read the workspace param with the '' fallback every module needs.

    GATEWAY_ARGSPEC already defaults workspace to "", but an explicit
    workspace=None from Ansible variable resolution would otherwise reach
    the SDK as None instead of "" — .get(...) or "" guards against that.
    """
    return module.params.get("workspace") or ""


def get_or_none(client: Any, name: str, workspace: str, module: AnsibleModule) -> Any:
    """Look up a sandbox by name/ID; return None on a confirmed NOT_FOUND,
    fail_json on any other error.

    Three call sites (sandbox.py's create/delete, sandbox_exec.py's name-
    to-ID resolution) each need "does this exist" semantics without
    conflating a real problem (gateway unreachable, bad credentials) with
    "confirmed absent" — treating every error as absence risks silently
    creating a duplicate sandbox, reporting a delete as already-done when
    it isn't, or masking a connectivity failure behind a confusing
    downstream error (live-verified: an unreachable gateway raising
    UNAVAILABLE must not be treated the same as NOT_FOUND). SandboxError
    itself is a bare RuntimeError with no status info, and this SDK
    version's get() doesn't actually raise it — grpc.RpcError propagates
    unwrapped — so it's treated as NOT_FOUND for lack of a finer signal,
    matching pre-existing behavior for that branch.
    """
    from openshell import SandboxError

    import grpc

    try:
        return client.get(name, workspace=workspace)
    except grpc.RpcError as e:
        if hasattr(e, "code") and e.code() == grpc.StatusCode.NOT_FOUND:
            return None
        module.fail_json(msg="failed to look up sandbox '%s': %s" % (name, e))
        return None
    except SandboxError:
        return None


def get_client(module: AnsibleModule) -> Any:
    """Create a SandboxClient from module params."""
    try:
        from openshell import SandboxClient
    except ImportError:
        module.fail_json(
            msg="The openshell Python SDK is required. Install it with: pip install 'openshell>=0.0.70'"
        )

    gateway_url = module.params["gateway"]
    parsed = urlparse(gateway_url)

    host = parsed.hostname or gateway_url
    if parsed.port:
        endpoint = f"{host}:{parsed.port}"
    elif parsed.scheme == "https":
        endpoint = f"{host}:443"
    elif parsed.scheme == "http":
        endpoint = f"{host}:80"
    else:
        endpoint = f"{host}:443"

    tls = build_tls_config(module)
    bearer = module.params.get("bearer_token")
    timeout = module.params.get("timeout") or 30.0

    return SandboxClient(
        endpoint=endpoint,
        tls=tls,
        bearer_token=bearer,
        timeout=timeout,
    )
