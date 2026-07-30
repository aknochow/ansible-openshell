#!/usr/bin/python
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: sandbox_upload
short_description: Upload a local directory into an OpenShell sandbox
description:
  - Uploads the contents of a local directory into a running sandbox.
  - Transfers over the same gRPC channel used for sandbox management
    (via C(SandboxClient.exec)'s stdin) rather than SSH/SFTP — avoids
    the per-file round-trip cost of copying a directory over SSH, and
    needs no sandbox-side tooling beyond C(tar) (already present in the
    community sandbox images).
version_added: "0.1.0"
author:
  - Adam Knochowski (@aknochow)
options:
  name:
    description:
      - Name of the sandbox to upload into.
    type: str
    required: true
  src:
    description:
      - Local directory to upload. Its contents (not the directory
        itself) land at O(dest) — matching M(ansible.builtin.copy)'s
        trailing-slash-source convention.
    type: path
    required: true
  dest:
    description:
      - Destination directory inside the sandbox. Created if it
        doesn't already exist.
    type: str
    required: true
extends_documentation_fragment:
  - aknochow.openshell.auth
requirements:
  - "openshell >= 0.0.70"
  - "python >= 3.12"
"""

EXAMPLES = r"""
- name: Upload a checkout into a sandbox
  aknochow.openshell.sandbox_upload:
    gateway: https://openshell.apps.example.com
    tls_cert: /certs/tls.crt
    tls_key: /certs/tls.key
    tls_ca: /certs/ca.crt
    name: "{{ sandbox.sandbox.name }}"
    src: /local/path/checkout
    dest: /sandbox/checkout
"""

RETURN = r"""
bytes_transferred:
  description: Size in bytes of the (gzip-compressed) archive transferred.
  type: int
  returned: always
"""

import io
import os
import tarfile

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
)

# Comfortably under gRPC's default ~1MB max message length — a single
# exec() call's stdin larger than that fails outright with OUT_OF_RANGE
# (confirmed live: a 2.7MB tar in one call errors; the same tar split
# into ~800KB chunks, each appended via a separate exec() call, does
# not). Each chunk is its own gRPC call, not a streamed upload — fine
# at this size (a few calls for a multi-MB checkout), but this isn't
# meant for arbitrarily large transfers.
CHUNK_SIZE = 800_000


def main():
    argument_spec = dict(
        name=dict(type="str", required=True),
        src=dict(type="path", required=True),
        dest=dict(type="str", required=True),
    )
    argument_spec.update(GATEWAY_ARGSPEC)

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=False,
    )

    from openshell import SandboxError

    try:
        import grpc
    except ImportError:
        module.fail_json(msg="grpcio is required")

    name = module.params["name"]
    src = module.params["src"]
    dest = module.params["dest"]
    workspace = module.params.get("workspace") or ""

    if not os.path.isdir(src):
        module.fail_json(msg="src '%s' is not a directory" % src)

    client = get_client(module)
    try:
        try:
            sandbox = client.get(name, workspace=workspace)
        except (SandboxError, grpc.RpcError) as e:
            module.fail_json(msg="sandbox '%s' not found: %s" % (name, e))
            return

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(src, arcname=".")
        tar_bytes = buf.getvalue()

        remote_tmp = "/tmp/.ansible-openshell-upload-%s.tar.gz" % os.urandom(8).hex()

        def run(argv, stdin=None):
            result = client.exec(sandbox.id, argv, stdin=stdin)
            if result.exit_code != 0:
                module.fail_json(
                    msg="command %s failed (rc=%d): %s"
                    % (argv, result.exit_code, result.stderr)
                )
            return result

        run(["mkdir", "-p", dest])
        for offset in range(0, len(tar_bytes), CHUNK_SIZE):
            run(["sh", "-c", "cat >> " + remote_tmp], stdin=tar_bytes[offset : offset + CHUNK_SIZE])
        run(["tar", "xzf", remote_tmp, "-C", dest])
        run(["rm", "-f", remote_tmp])

        module.exit_json(changed=True, bytes_transferred=len(tar_bytes))
    except (SandboxError, grpc.RpcError) as e:
        module.fail_json(msg=str(e))
    finally:
        client.close()


if __name__ == "__main__":
    main()
