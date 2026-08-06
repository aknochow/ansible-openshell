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
    """Return the sandbox ref, None on confirmed NOT_FOUND, or fail_json on any other error.

    SandboxError (bare RuntimeError, no status info) is treated as NOT_FOUND
    for backward compatibility — the SDK doesn't raise it in practice today.
    """
    from openshell import SandboxError

    import grpc

    try:
        return client.get(name, workspace=workspace)
    except grpc.RpcError as e:
        if hasattr(e, "code") and e.code() == grpc.StatusCode.NOT_FOUND:
            return None
        # Deliberately not broadening this to also treat e.g.
        # INVALID_ARGUMENT as "not found": sandbox_exec.py's name-or-ID
        # fallback would benefit (an ID that fails a by-name lookup with
        # something other than NOT_FOUND), but create/delete_sandbox use
        # this same helper for a genuinely different purpose — a user's
        # malformed `name` triggering INVALID_ARGUMENT should fail loudly,
        # not be silently treated as "doesn't exist yet" and risk creating
        # a duplicate. Confirmed live against the real gateway that an
        # ID passed as a name lookup returns NOT_FOUND, not
        # INVALID_ARGUMENT, so this hasn't been an issue in practice.
        module.fail_json(msg="failed to look up sandbox '%s': %s" % (name, e))
        return None  # unreachable — fail_json raises SystemExit
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
        return

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
