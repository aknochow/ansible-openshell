#!/usr/bin/python
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: sandbox_exec
short_description: Execute a command in an OpenShell sandbox
description:
  - Execute a command inside an existing OpenShell sandbox and return the output.
  - The sandbox must already exist and be in the READY phase.
version_added: "0.1.0"
author:
  - Adam Knochowski (@aknochow)
options:
  sandbox:
    description:
      - Name or ID of the sandbox to execute the command in.
    type: str
    required: true
  command:
    description:
      - Command to execute as a list of arguments.
    type: list
    elements: str
    required: true
  workdir:
    description:
      - Working directory for command execution inside the sandbox.
    type: str
  environment:
    description:
      - Environment variables to set for the command.
    type: dict
    default: {}
  stdin:
    description:
      - Standard input to send to the command.
    type: str
  command_timeout:
    description:
      - Timeout in seconds for the command execution.
      - Separate from the gRPC O(timeout) which controls the connection.
    type: int
extends_documentation_fragment:
  - aknochow.openshell.auth
requirements:
  - "openshell >= 0.0.70"
  - "python >= 3.12"
"""

EXAMPLES = r"""
- name: Run a command in a sandbox
  aknochow.openshell.sandbox_exec:
    gateway: https://openshell.apps.example.com
    sandbox: "{{ sandbox.sandbox.name }}"
    command:
      - echo
      - hello world
    bearer_token: "{{ openshell_token }}"
  register: result

- name: Run a Python script in a sandbox
  aknochow.openshell.sandbox_exec:
    gateway: https://openshell.apps.example.com
    sandbox: my-sandbox
    command:
      - python3
      - -c
      - "print('hello from sandbox')"
    workdir: /workspace
    environment:
      PYTHONPATH: /app
    command_timeout: 120
    bearer_token: "{{ openshell_token }}"
  register: result

- name: Check command exit code
  ansible.builtin.debug:
    msg: "Command exited with {{ result.rc }}, stdout: {{ result.stdout }}"
"""

RETURN = r"""
rc:
  description: Command exit code.
  type: int
  returned: always
stdout:
  description: Standard output from the command.
  type: str
  returned: always
stderr:
  description: Standard error from the command.
  type: str
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
)


def main():
    argument_spec = dict(
        sandbox=dict(type="str", required=True),
        command=dict(type="list", elements="str", required=True),
        workdir=dict(type="str"),
        environment=dict(type="dict", default={}),
        stdin=dict(type="str", no_log=False),
        command_timeout=dict(type="int"),
    )
    argument_spec.update(GATEWAY_ARGSPEC)

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=False,
    )

    client = get_client(module)
    try:
        import grpc
        from openshell import SandboxError

        sandbox_name = module.params["sandbox"]
        command = module.params["command"]
        workdir = module.params.get("workdir")
        env = module.params.get("environment") or {}
        stdin_data = module.params.get("stdin")
        cmd_timeout = module.params.get("command_timeout")
        workspace = module.params.get("workspace") or ""

        try:
            # SandboxClient.exec() takes a globally-unique sandbox_id and has
            # no workspace kwarg — only client.get() is workspace-scoped
            # (sandbox names are only unique within a workspace, not
            # globally). Resolve `sandbox` (name or ID, per this module's
            # own docs/examples) through get() first so a bare name is
            # disambiguated within the caller's requested workspace rather
            # than passed straight to exec() and silently hitting a
            # same-named sandbox elsewhere. get() only resolves names
            # though (confirmed live: passing an ID raises NOT_FOUND), so
            # fall back to treating `sandbox` as an already-resolved ID —
            # exec() itself will raise loudly if it's neither.
            try:
                sandbox_id = client.get(sandbox_name, workspace=workspace).id
            except (SandboxError, grpc.RpcError):
                sandbox_id = sandbox_name
            result = client.exec(
                sandbox_id,
                command,
                workdir=workdir,
                env=env,
                stdin=stdin_data.encode() if stdin_data else None,
                timeout_seconds=cmd_timeout,
            )
            module.exit_json(
                changed=True,
                rc=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except (SandboxError, grpc.RpcError) as e:
            module.fail_json(msg=str(e))
    finally:
        client.close()


if __name__ == "__main__":
    main()
