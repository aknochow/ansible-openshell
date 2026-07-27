#!/usr/bin/python
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: provider
short_description: Manage OpenShell providers
description:
  - Create, update, or delete provider credential bundles on an OpenShell gateway.
  - Uses the OpenShell gRPC API via the SDK's proto bindings.
version_added: "0.1.0"
author:
  - Adam Knochowski (@aknochow)
options:
  name:
    description:
      - Name of the provider.
    type: str
    required: true
  type:
    description:
      - Provider type identifier (e.g., V(claude), V(gitlab), V(github)).
      - Required when O(state=present).
    type: str
  credentials:
    description:
      - Credential key-value pairs for the provider.
      - Values are treated as sensitive and will not be logged.
    type: dict
    default: {}
  config:
    description:
      - Additional configuration key-value pairs for the provider.
    type: dict
    default: {}
  state:
    description:
      - Desired state of the provider.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - aknochow.openshell.auth
requirements:
  - "openshell >= 0.0.70"
  - "python >= 3.12"
"""

EXAMPLES = r"""
- name: Create a Claude provider
  aknochow.openshell.provider:
    gateway: https://openshell.apps.example.com
    name: claude
    type: claude
    credentials:
      api_key: "{{ anthropic_api_key }}"
    bearer_token: "{{ openshell_token }}"
    state: present

- name: Delete a provider
  aknochow.openshell.provider:
    gateway: https://openshell.apps.example.com
    name: claude
    bearer_token: "{{ openshell_token }}"
    state: absent
"""

RETURN = r"""
provider:
  description: Provider details.
  type: dict
  returned: when state=present
  contains:
    name:
      description: Provider name.
      type: str
    type:
      description: Provider type.
      type: str
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
)


def provider_to_dict(proto_provider):
    return dict(
        name=proto_provider.metadata.name,
        type=proto_provider.type,
    )


def main():
    argument_spec = dict(
        name=dict(type="str", required=True),
        type=dict(type="str"),
        credentials=dict(type="dict", default={}, no_log=True),
        config=dict(type="dict", default={}),
        state=dict(type="str", choices=["present", "absent"], default="present"),
    )
    argument_spec.update(GATEWAY_ARGSPEC)

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=False,
    )

    client = get_client(module)
    try:
        from openshell._proto import datamodel_pb2, openshell_pb2, openshell_pb2_grpc

        stub = openshell_pb2_grpc.OpenShellStub(client._channel)
        name = module.params["name"]
        state = module.params["state"]

        if state == "present":
            provider_type = module.params.get("type")
            if not provider_type:
                module.fail_json(msg="'type' is required when state=present")

            credentials = module.params.get("credentials") or {}
            config = module.params.get("config") or {}

            try:
                resp = stub.GetProvider(
                    openshell_pb2.GetProviderRequest(name=name),
                    timeout=client._timeout,
                )
                stub.UpdateProvider(
                    openshell_pb2.UpdateProviderRequest(
                        provider=datamodel_pb2.Provider(
                            metadata=datamodel_pb2.ObjectMeta(name=name),
                            type=provider_type,
                            credentials=credentials,
                            config=config,
                        ),
                    ),
                    timeout=client._timeout,
                )
                resp = stub.GetProvider(
                    openshell_pb2.GetProviderRequest(name=name),
                    timeout=client._timeout,
                )
                module.exit_json(changed=True, provider=provider_to_dict(resp.provider))
            except Exception:
                try:
                    resp = stub.CreateProvider(
                        openshell_pb2.CreateProviderRequest(
                            provider=datamodel_pb2.Provider(
                                metadata=datamodel_pb2.ObjectMeta(name=name),
                                type=provider_type,
                                credentials=credentials,
                                config=config,
                            ),
                        ),
                        timeout=client._timeout,
                    )
                    module.exit_json(changed=True, provider=provider_to_dict(resp.provider))
                except Exception as e:
                    module.fail_json(msg=f"Failed to create provider: {e}")

        else:
            try:
                stub.GetProvider(
                    openshell_pb2.GetProviderRequest(name=name),
                    timeout=client._timeout,
                )
            except Exception:
                module.exit_json(changed=False)

            try:
                stub.DeleteProvider(
                    openshell_pb2.DeleteProviderRequest(name=name),
                    timeout=client._timeout,
                )
                module.exit_json(changed=True)
            except Exception as e:
                module.fail_json(msg=f"Failed to delete provider: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
