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
import tarfile
from typing import Any

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
    GATEWAY_ARGSPEC,
    get_client,
    get_or_none,
    get_workspace,
)

# Comfortably under gRPC's default ~1MB max message length — a single
# exec() call's stdin larger than that fails outright with OUT_OF_RANGE
# (confirmed live: a 2.7MB tar in one call errors; the same tar split
# into ~800KB chunks, each appended via a separate exec() call, does
# not). Each chunk is its own gRPC call, not a streamed upload — fine
# at this size (a few calls for a multi-MB checkout), but this isn't
# meant for arbitrarily large transfers.
CHUNK_SIZE = 800_000

# The whole archive is built in memory (io.BytesIO) before any chunking
# happens, so an oversized src silently balloons controller memory before
# CHUNK_SIZE ever comes into play. This is a guardrail against pointing
# src at the wrong directory, not a real transfer size limit — 100MB
# already implies thousands of round-trip exec() calls at CHUNK_SIZE.
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

# Each chunk is appended via this instead of shell `cat >> <path>`: shell
# redirection has no way to say "only if this path is still a regular
# file, not a symlink something else in the sandbox swapped in since the
# last chunk" -- `cat >>`/`dd` both follow a symlink transparently. This
# opens with O_NOFOLLOW, which fails loudly (ELOOP) instead of silently
# writing through a symlink if the target changed between chunks. mktemp
# already makes the filename unpredictable (see below); this closes the
# remaining window where a process already inside the sandbox that
# *observes* the resulting name could still race a later chunk.
_APPEND_NOFOLLOW_SCRIPT = (
    "import os, sys\n"
    "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)\n"
    "try:\n"
    "    os.write(fd, sys.stdin.buffer.read())\n"
    "finally:\n"
    "    os.close(fd)\n"
)


def reject_escaping_members(tarinfo: tarfile.TarInfo, real_src: str) -> tarfile.TarInfo | None:
    """Drop tar members whose resolved path escapes the source directory."""
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
    # A hard link's target can live anywhere on the same filesystem,
    # including outside real_src, and there's no way to tell from this
    # end whether the same inode is ALSO reachable from outside real_src
    # -- confirmed empirically that tarfile.add() only emits an LNKTYPE
    # member when it already recorded the same inode earlier in this
    # exact walk; a hard link whose only other name lives outside
    # real_src is archived as an ordinary file with that outside
    # content, not flagged as a link at all. st_nlink > 1 is the only
    # signal available; reject rather than risk silently including
    # content from outside real_src.
    #
    # This check applies to both REGTYPE (tarfile's first sighting of an
    # inode) and LNKTYPE (a later sighting of the SAME inode within this
    # walk) members -- checking tarinfo.isfile() alone misses LNKTYPE
    # members entirely, which would leave a hardlink reference pointing
    # at a REGTYPE member this same filter already dropped, a dangling
    # reference on extraction. Directories and symlinks (already handled
    # above) are exempt: they're not affected by this multi-link
    # ambiguity.
    if not tarinfo.isdir() and not tarinfo.issym():
        try:
            st = os.stat(full, follow_symlinks=False)
        except OSError:
            return None
        if st.st_nlink > 1:
            return None
    return tarinfo


def exec_or_fail(
    client: Any, sandbox_id: str, module: AnsibleModule, argv: list[str], stdin: bytes | None = None
) -> Any:
    """Run a command in the sandbox, calling module.fail_json on non-zero exit."""
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
    workspace = get_workspace(module)

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
        sandbox = get_or_none(client, name, workspace, module)
        if sandbox is None:
            module.fail_json(msg="sandbox '%s' not found" % name)
            return

        real_src = os.path.realpath(src)

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            # filter is TarFile.add()'s own hook (single TarInfo arg, returns
            # TarInfo or None) — not the 2-arg extraction-filter signature
            # (tarfile.data_filter). tarfile.add() does NOT dereference
            # symlinks by default (confirmed empirically: it archives the
            # link itself, not the target's content) — see
            # reject_escaping_members for why that still needs guarding.
            tf.add(
                src,
                arcname=".",
                filter=lambda tarinfo: reject_escaping_members(tarinfo, real_src),
            )
        tar_bytes = buf.getvalue()
        if len(tar_bytes) > MAX_ARCHIVE_BYTES:
            module.fail_json(
                msg="compressed archive is %d bytes, exceeding the %d byte limit"
                % (len(tar_bytes), MAX_ARCHIVE_BYTES)
            )

        # mktemp (not a locally-constructed random name) so the remote
        # path is created atomically — a predictable name written via a
        # plain `cat >>` redirect is vulnerable to a symlink race if
        # something else in the sandbox can predict/pre-create it.
        mktemp_result = exec_or_fail(
            client, sandbox.id, module, ["mktemp", "/tmp/.ansible-openshell-upload-XXXXXXXX.tar.gz"]
        )
        remote_tmp = mktemp_result.stdout.strip()
        tmp_suffix = remote_tmp[len("/tmp/.ansible-openshell-upload-") :]
        # Full character-class + suffix match rather than just excluding
        # '/' and '..' — a compromised mktemp could otherwise still return
        # something with shell metacharacters or missing the .tar.gz
        # extension that later steps assume.
        has_expected_prefix = remote_tmp.startswith("/tmp/.ansible-openshell-upload-")
        has_expected_suffix = re.fullmatch(r"[A-Za-z0-9._-]+\.tar\.gz", tmp_suffix)
        if not has_expected_prefix or not has_expected_suffix:
            module.fail_json(msg="mktemp returned an unexpected path: %r" % remote_tmp)

        try:
            exec_or_fail(client, sandbox.id, module, ["mkdir", "-p", dest])
            for offset in range(0, len(tar_bytes), CHUNK_SIZE):
                exec_or_fail(
                    client,
                    sandbox.id,
                    module,
                    ["python3", "-c", _APPEND_NOFOLLOW_SCRIPT, remote_tmp],
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
        # Best-effort — an exception raised here (rather than returned)
        # would propagate past this function uncaught, masking whatever
        # error (if any) the try block above already reported.
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
