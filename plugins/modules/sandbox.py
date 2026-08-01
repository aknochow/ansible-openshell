#!/usr/bin/python
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: sandbox
short_description: Manage OpenShell sandboxes
description:
  - Create or delete OpenShell sandboxes via the gateway gRPC API.
  - Uses the official OpenShell Python SDK.
version_added: "0.1.0"
author:
  - Adam Knochowski (@aknochow)
options:
  name:
    description:
      - Name of the sandbox.
      - Required when O(state=absent).
      - When O(state=present) and omitted, the gateway assigns a name.
    type: str
  image:
    description:
      - OCI image reference for the sandbox container.
      - Required when O(state=present) and no existing sandbox with O(name) exists.
    type: str
  environment:
    description:
      - Environment variables to inject into the sandbox.
    type: dict
    default: {}
  providers:
    description:
      - List of provider names to attach to the sandbox.
      - Providers must already be configured on the gateway.
    type: list
    elements: str
    default: []
  policy:
    description:
      - Sandbox policy overrides, applied at creation time.
      - Currently supports filesystem (Landlock) read-only/read-write
        path overrides. Omit to use the gateway's default policy.
      - See U(https://github.com/NVIDIA/OpenShell) for the full policy
        model — network egress and process-identity overrides aren't
        exposed by this module yet.
    type: dict
    default: {}
    suboptions:
      filesystem:
        description:
          - Filesystem (Landlock) path overrides.
        type: dict
        suboptions:
          read_only:
            description:
              - Paths to grant read-only access to, in addition to the
                gateway's default policy.
            type: list
            elements: str
          read_write:
            description:
              - Paths to grant read-write access to, in addition to the
                gateway's default policy. Some gateway default policies
                mark C(/dev) read-write without extending that to
                C(/dev/shm) (a separate tmpfs mount) — add it explicitly
                here if a sandboxed process needs to write there (e.g.
                tools that use shared-memory temp files).
            type: list
            elements: str
  state:
    description:
      - Desired state of the sandbox.
    type: str
    choices: [present, absent]
    default: present
  wait:
    description:
      - Whether to wait for the sandbox to reach the READY phase after creation.
    type: bool
    default: true
  wait_timeout:
    description:
      - Maximum time in seconds to wait for the sandbox to become ready.
    type: int
    default: 300
extends_documentation_fragment:
  - aknochow.openshell.auth
requirements:
  - "openshell >= 0.0.70"
  - "python >= 3.12"
"""

EXAMPLES = r"""
- name: Create a sandbox
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    image: docker.io/library/python:3.12-slim
    tls_cert: /certs/tls.crt
    tls_key: /certs/tls.key
    tls_ca: /certs/ca.crt
    state: present
  register: sandbox

- name: Create a sandbox with environment variables
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    image: docker.io/library/python:3.12-slim
    environment:
      MY_VAR: my_value
    bearer_token: "{{ openshell_token }}"
    state: present
  register: sandbox

- name: Create a sandbox with a writable /dev/shm (e.g. for tools that use shared-memory temp files)
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    image: docker.io/library/python:3.12-slim
    policy:
      filesystem:
        read_write: ["/dev/shm"]
    bearer_token: "{{ openshell_token }}"
    state: present
  register: sandbox

- name: Delete a sandbox
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    name: "{{ sandbox.sandbox.name }}"
    bearer_token: "{{ openshell_token }}"
    state: absent
"""

RETURN = r"""
sandbox:
  description: Sandbox details.
  type: dict
  returned: when state=present
  contains:
    id:
      description: Sandbox unique identifier.
      type: str
      returned: always
    name:
      description: Sandbox name.
      type: str
      returned: always
    workspace:
      description: Workspace the sandbox belongs to.
      type: str
      returned: always
    phase:
      description: Current sandbox phase (e.g., READY, PROVISIONING, ERROR).
      type: str
      returned: always
    policy_version:
      description: Current policy version applied to the sandbox.
      type: int
      returned: always
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
    get_workspace,
    sandbox_to_dict,
)


def create_sandbox(module, client):
    from openshell import SandboxError

    try:
        import grpc
    except ImportError:
        module.fail_json(msg="grpcio is required")

    try:
        from openshell._proto import openshell_pb2, sandbox_pb2
    except ImportError:
        module.fail_json(msg="openshell proto bindings not found")

    image = module.params.get("image")
    if not image:
        module.fail_json(msg="'image' is required when state=present")

    environment = module.params.get("environment") or {}
    providers = module.params.get("providers") or []
    name = module.params.get("name")
    policy = module.params.get("policy") or {}
    workspace = get_workspace(module)

    template = openshell_pb2.SandboxTemplate(image=image)
    spec_kwargs = dict(
        template=template,
        environment=environment,
        providers=providers,
    )

    # Only network_policies/filesystem/landlock/process are on the
    # wire today — filesystem is the only one this module exposes so
    # far (see the 'policy' option docs for why: gateway defaults can
    # mark /dev read-write without extending that to the separate
    # /dev/shm tmpfs mount, which some tools need).
    filesystem_policy = policy.get("filesystem") or {}
    if filesystem_policy:
        spec_kwargs["policy"] = sandbox_pb2.SandboxPolicy(
            filesystem=sandbox_pb2.FilesystemPolicy(
                read_only=filesystem_policy.get("read_only") or [],
                read_write=filesystem_policy.get("read_write") or [],
            )
        )

    spec = openshell_pb2.SandboxSpec(**spec_kwargs)

    try:
        if name:
            try:
                existing = client.get(name, workspace=workspace)
                module.exit_json(changed=False, sandbox=sandbox_to_dict(existing))
                return
            except (SandboxError, grpc.RpcError):
                pass

        # client.create() is the SDK's own public wrapper for CreateSandbox —
        # it builds the SandboxRef (workspace included) from the response and
        # raises SandboxError if the gateway returns an empty id, so there's
        # no need to reach into client._channel/_timeout and reimplement the
        # RPC call by hand.
        ref = client.create(workspace=workspace, spec=spec, name=name)

        if module.params.get("wait"):
            timeout = module.params.get("wait_timeout") or 300
            # Use the workspace the gateway actually recorded on the sandbox
            # (ref.workspace), not the request's — the two should always
            # agree, but the ref is the server's own source of truth.
            ref = client.wait_ready(ref.name, workspace=ref.workspace, timeout_seconds=float(timeout))

        module.exit_json(changed=True, sandbox=sandbox_to_dict(ref))
    except (SandboxError, grpc.RpcError) as e:
        module.fail_json(msg=str(e))


def delete_sandbox(module, client):
    from openshell import SandboxError

    try:
        import grpc
    except ImportError:
        module.fail_json(msg="grpcio is required")

    name = module.params.get("name")
    if not name:
        module.fail_json(msg="'name' is required when state=absent")
    workspace = get_workspace(module)

    try:
        client.get(name, workspace=workspace)
    except (SandboxError, grpc.RpcError):
        module.exit_json(changed=False)
        return

    try:
        client.delete(name, workspace=workspace)
        if module.params.get("wait"):
            timeout = module.params.get("wait_timeout") or 60
            client.wait_deleted(name, workspace=workspace, timeout_seconds=float(timeout))
        module.exit_json(changed=True)
    except (SandboxError, grpc.RpcError) as e:
        module.fail_json(msg=str(e))


def main():
    argument_spec = dict(
        name=dict(type="str"),
        image=dict(type="str"),
        environment=dict(type="dict", default={}),
        providers=dict(type="list", elements="str", default=[]),
        policy=dict(type="dict", default={}),
        state=dict(type="str", choices=["present", "absent"], default="present"),
        wait=dict(type="bool", default=True),
        wait_timeout=dict(type="int", default=300),
    )
    argument_spec.update(GATEWAY_ARGSPEC)

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=False,
    )

    client = get_client(module)
    try:
        if module.params["state"] == "present":
            create_sandbox(module, client)
        else:
            delete_sandbox(module, client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
