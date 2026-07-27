#!/usr/bin/python
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: sandbox_info
short_description: Gather information about OpenShell sandboxes
description:
  - Retrieve details about one or all sandboxes from an OpenShell gateway.
version_added: "0.1.0"
author:
  - Adam Knochowski (@aknochow)
options:
  name:
    description:
      - Name of a specific sandbox to retrieve.
      - If omitted, all sandboxes are listed.
    type: str
extends_documentation_fragment:
  - aknochow.openshell.auth
requirements:
  - "openshell >= 0.0.70"
  - "python >= 3.12"
"""

EXAMPLES = r"""
- name: List all sandboxes
  aknochow.openshell.sandbox_info:
    gateway: https://openshell.apps.example.com
    bearer_token: "{{ openshell_token }}"
  register: result

- name: Get a specific sandbox
  aknochow.openshell.sandbox_info:
    gateway: https://openshell.apps.example.com
    name: my-sandbox
    bearer_token: "{{ openshell_token }}"
  register: result
"""

RETURN = r"""
sandboxes:
  description: List of sandbox details.
  type: list
  elements: dict
  returned: always
  contains:
    id:
      description: Sandbox unique identifier.
      type: str
    name:
      description: Sandbox name.
      type: str
    phase:
      description: Current sandbox phase.
      type: str
    policy_version:
      description: Current policy version.
      type: int
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
)

PHASE_NAMES = {
    0: "UNSPECIFIED",
    1: "PROVISIONING",
    2: "READY",
    3: "ERROR",
    4: "DELETING",
    5: "UNKNOWN",
}


def sandbox_to_dict(ref):
    return dict(
        id=ref.id,
        name=ref.name,
        phase=PHASE_NAMES.get(ref.phase, str(ref.phase)),
        policy_version=ref.current_policy_version,
    )


def main():
    argument_spec = dict(
        name=dict(type="str"),
    )
    argument_spec.update(GATEWAY_ARGSPEC)

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_client(module)
    try:
        import grpc
        from openshell import SandboxError

        name = module.params.get("name")

        try:
            if name:
                ref = client.get(name)
                module.exit_json(changed=False, sandboxes=[sandbox_to_dict(ref)])
            else:
                refs = client.list()
                module.exit_json(
                    changed=False,
                    sandboxes=[sandbox_to_dict(r) for r in refs],
                )
        except (SandboxError, grpc.RpcError) as e:
            module.fail_json(msg=str(e))
    finally:
        client.close()


if __name__ == "__main__":
    main()
