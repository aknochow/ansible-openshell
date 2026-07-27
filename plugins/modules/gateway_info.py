#!/usr/bin/python
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: gateway_info
short_description: Gather information about an OpenShell gateway
description:
  - Retrieve health and version information from an OpenShell gateway.
version_added: "0.1.0"
author:
  - Adam Knochowski (@aknochow)
extends_documentation_fragment:
  - aknochow.openshell.auth
requirements:
  - "openshell >= 0.0.70"
  - "python >= 3.12"
"""

EXAMPLES = r"""
- name: Check gateway health
  aknochow.openshell.gateway_info:
    gateway: https://openshell.apps.example.com
    bearer_token: "{{ openshell_token }}"
  register: gw

- name: Fail if gateway is unhealthy
  ansible.builtin.fail:
    msg: "Gateway is not healthy"
  when: not gw.healthy
"""

RETURN = r"""
status:
  description: Gateway health status string.
  type: str
  returned: always
version:
  description: Gateway version string.
  type: str
  returned: always
healthy:
  description: Whether the gateway is healthy.
  type: bool
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
)

STATUS_NAMES = {
    0: "UNSPECIFIED",
    1: "HEALTHY",
    2: "DEGRADED",
    3: "UNHEALTHY",
}


def main():
    argument_spec = dict()
    argument_spec.update(GATEWAY_ARGSPEC)

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_client(module)
    try:
        import grpc
        from openshell import SandboxError

        try:
            health = client.health()
            status_val = health.status if isinstance(health.status, int) else health.status
            status_name = STATUS_NAMES.get(status_val, str(status_val))
            module.exit_json(
                changed=False,
                status=status_name,
                version=health.version,
                healthy=(status_val == 1),
            )
        except (SandboxError, grpc.RpcError) as e:
            module.fail_json(msg=str(e))
    finally:
        client.close()


if __name__ == "__main__":
    main()
