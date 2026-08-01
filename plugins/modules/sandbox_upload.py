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
import posixpath
import re
import shlex
import tarfile
from typing import Any

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


def reject_escaping_members(tarinfo: tarfile.TarInfo, real_src: str) -> tarfile.TarInfo | None:
    """Drop tar members whose resolved path escapes the source directory.

    This is a TarFile.add() filter hook (single TarInfo arg, returns
    TarInfo or None — NOT the 2-arg (member, path) signature specific
    to extraction filters like tarfile.data_filter, a different hook
    for a different operation). Guards against a symlink planted
    inside a malicious checkout pointing outside it — tarfile.add()
    does NOT dereference symlinks by default (confirmed: it archives
    the link itself, not the target's content), so the real risk
    isn't embedded content but the archived symlink being recreated
    verbatim by `tar xzf` on the sandbox side, landing an
    attacker-chosen absolute path inside the sandbox filesystem.
    """
    # tarinfo.name is always relative here (tarfile.add() derives it
    # from arcname during its own directory walk), but reject an
    # absolute name outright rather than let os.path.join silently
    # discard `real_src` and resolve outside it.
    if os.path.isabs(tarinfo.name):
        return None
    # An absolute symlink target can resolve inside `real_src` on the
    # controller (passing the realpath check below) yet still be
    # archived with that literal absolute linkname — confirmed
    # live: `tar xzf` recreates it verbatim on the sandbox side,
    # where the same absolute path means something else entirely
    # (or nothing at all). Relative targets are already covered by
    # the realpath check, since it resolves the member's own
    # location (following its symlink chain) against `real_src`.
    if tarinfo.issym() and os.path.isabs(tarinfo.linkname):
        return None
    full = os.path.realpath(os.path.join(real_src, tarinfo.name))
    if full != real_src and not full.startswith(real_src + os.sep):
        return None
    return tarinfo


def exec_or_fail(
    client, sandbox_id: str, module: AnsibleModule, argv: list[str], stdin: bytes | None = None
) -> Any:
    result = client.exec(sandbox_id, argv, stdin=stdin)
    if result.exit_code != 0:
        module.fail_json(
            msg="command %s failed (rc=%d): %s" % (argv, result.exit_code, result.stderr)
        )
    return result


def main() -> None:
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

    try:
        from openshell import SandboxError
    except ImportError:
        module.fail_json(msg="The openshell Python SDK is required. Install it with: pip install 'openshell>=0.0.70'")
        return

    try:
        import grpc
    except ImportError:
        module.fail_json(msg="grpcio is required")
        return

    name = module.params["name"]
    src = module.params["src"]
    dest = module.params["dest"]
    # Matches sandbox.py/sandbox_info.py's defensive fallback — GATEWAY_ARGSPEC
    # already defaults workspace to "", but an explicit workspace=None from
    # Ansible variable resolution would otherwise reach the SDK as None.
    workspace = module.params.get("workspace") or ""

    if not os.path.isdir(src):
        module.fail_json(msg="src '%s' is not a directory" % src)
    # Reject '..' in the raw input before normalizing — dest isn't confined
    # to any particular base directory (the operator picks the destination
    # deliberately, same trust model as ansible.builtin.copy's dest), so
    # this isn't a traversal boundary; it's a well-formedness check, and
    # checking post-normpath would be checking for something normpath
    # already resolved away.
    if ".." in dest.split("/"):
        module.fail_json(msg="dest must not contain '..' components, got '%s'" % dest)
    # dest is always a remote POSIX (Linux sandbox) path regardless of
    # what OS the controller runs on — os.path/os.sep would use '\' on a
    # Windows controller and silently fail to split on '/' at all.
    dest = posixpath.normpath(dest)
    if not dest.startswith("/"):
        module.fail_json(msg="dest must be an absolute path, got '%s'" % dest)

    client = get_client(module)
    try:
        try:
            sandbox = client.get(name, workspace=workspace)
        except (SandboxError, grpc.RpcError) as e:
            module.fail_json(msg="sandbox '%s' not found: %s" % (name, e))
            return

        real_src = os.path.realpath(src)

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(
                src,
                arcname=".",
                filter=lambda tarinfo: reject_escaping_members(tarinfo, real_src),
            )
        tar_bytes = buf.getvalue()

        # mktemp (not a locally-constructed random name) so the remote
        # path is created atomically — a predictable name written via a
        # plain `cat >>` redirect is vulnerable to a symlink race if
        # something else in the sandbox can predict/pre-create it.
        mktemp_result = exec_or_fail(
            client, sandbox.id, module, ["mktemp", "/tmp/.ansible-openshell-upload-XXXXXXXX.tar.gz"]
        )
        remote_tmp = mktemp_result.stdout.strip()
        _tmp_suffix = remote_tmp[len("/tmp/.ansible-openshell-upload-") :]
        # Full character-class + suffix match rather than just excluding
        # '/' and '..' — a compromised mktemp could otherwise still return
        # something with shell metacharacters or missing the .tar.gz
        # extension that later steps assume.
        if not remote_tmp.startswith("/tmp/.ansible-openshell-upload-") or not re.fullmatch(
            r"[A-Za-z0-9._-]+\.tar\.gz", _tmp_suffix
        ):
            module.fail_json(msg="mktemp returned an unexpected path: %r" % remote_tmp)

        try:
            exec_or_fail(client, sandbox.id, module, ["mkdir", "-p", dest])
            for offset in range(0, len(tar_bytes), CHUNK_SIZE):
                exec_or_fail(
                    client,
                    sandbox.id,
                    module,
                    ["sh", "-c", "cat >> " + shlex.quote(remote_tmp)],
                    stdin=tar_bytes[offset : offset + CHUNK_SIZE],
                )
            exec_or_fail(client, sandbox.id, module, ["tar", "xzf", remote_tmp, "-C", dest])
        finally:
            # Best-effort — if this fails too, the original error (if
            # any) from the block above is what module.fail_json already
            # raised; don't let a cleanup failure mask it.
            try:
                client.exec(sandbox.id, ["rm", "-f", remote_tmp])
            except Exception:
                pass

        module.exit_json(changed=True, bytes_transferred=len(tar_bytes))
    except (SandboxError, grpc.RpcError) as e:
        module.fail_json(msg=str(e))
    finally:
        client.close()


if __name__ == "__main__":
    main()
