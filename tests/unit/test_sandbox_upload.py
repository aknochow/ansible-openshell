# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_openshell():
    mock_sdk = MagicMock()
    mock_sdk.SandboxError = type("SandboxError", (RuntimeError,), {})
    sys.modules["openshell"] = mock_sdk
    yield mock_sdk
    sys.modules.pop("openshell", None)


@pytest.fixture
def tmp_src(tmp_path):
    """A real source tree: a normal file plus a subdirectory."""
    (tmp_path / "normal.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested")
    return tmp_path


def _filter_paths(real_src):
    """Run a real tarfile.add() over real_src with the module's own filter,
    returning the set of member names that survived."""
    from ansible_collections.aknochow.openshell.plugins.modules.sandbox_upload import (
        reject_escaping_members,
    )

    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(
            real_src,
            arcname=".",
            filter=lambda tarinfo: reject_escaping_members(tarinfo, real_src),
        )
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        return {m.name for m in tf.getmembers()}


class TestRejectEscapingMembers:
    def test_normal_files_kept(self, mock_openshell, tmp_src):
        real_src = os.path.realpath(str(tmp_src))
        names = _filter_paths(real_src)

        assert "./normal.txt" in names
        assert "./sub/nested.txt" in names

    def test_absolute_symlink_target_rejected(self, mock_openshell, tmp_src):
        real_src = os.path.realpath(str(tmp_src))
        os.symlink("/etc/passwd", os.path.join(real_src, "evil_link"))

        names = _filter_paths(real_src)

        assert "./evil_link" not in names
        assert "./normal.txt" in names

    def test_symlink_escaping_real_src_rejected(self, mock_openshell, tmp_src):
        real_src = os.path.realpath(str(tmp_src))
        outside_dir = tempfile.mkdtemp()
        try:
            outside_file = os.path.join(outside_dir, "secret.txt")
            with open(outside_file, "w") as f:
                f.write("outside secret")
            os.symlink(outside_file, os.path.join(real_src, "relative_escape"))

            names = _filter_paths(real_src)

            assert "./relative_escape" not in names
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_hardlink_to_outside_file_rejected(self, mock_openshell, tmp_src):
        # Regression check: a hard link inside real_src pointing at a file
        # OUTSIDE real_src previously got archived as a plain regular file
        # containing the outside file's real content -- tarfile.add() only
        # emits an LNKTYPE member when it already recorded the same inode
        # earlier in THIS walk, so a hard link whose only other name lives
        # outside real_src is otherwise indistinguishable from a normal
        # file to the filter. Fails against the pre-fix filter (the
        # hardlinked file's content sails through); passes once nlink > 1
        # is rejected.
        real_src = os.path.realpath(str(tmp_src))
        outside_dir = tempfile.mkdtemp()
        try:
            outside_file = os.path.join(outside_dir, "secret.txt")
            with open(outside_file, "w") as f:
                f.write("outside secret")
            hardlink_path = os.path.join(real_src, "hardlinked.txt")
            os.link(outside_file, hardlink_path)

            names = _filter_paths(real_src)

            assert "./hardlinked.txt" not in names
            assert "./normal.txt" in names
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_hardlink_within_real_src_also_rejected(self, mock_openshell, tmp_src):
        # A conservative choice, documented in the fix itself: nlink > 1 is
        # rejected regardless of where the other name lives, since tarfile
        # can't tell the difference from here. An intra-real_src hard link
        # is collateral, not the target -- worth knowing this test exists
        # if that tradeoff ever needs revisiting.
        real_src = os.path.realpath(str(tmp_src))
        os.link(os.path.join(real_src, "normal.txt"), os.path.join(real_src, "normal_hardlink.txt"))

        names = _filter_paths(real_src)

        assert "./normal_hardlink.txt" not in names


def _run_upload_main(module_params, tar_bytes_len_forcing_chunks=False):
    from ansible_collections.aknochow.openshell.plugins.modules import sandbox_upload as upload_module

    fake_module = MagicMock()
    fake_module.params = module_params
    fake_client = MagicMock()
    fake_sandbox = MagicMock()
    fake_sandbox.id = "sandbox-123"

    exec_calls = []

    def fake_exec(sandbox_id, argv, stdin=None):
        exec_calls.append((sandbox_id, argv, stdin))
        result = MagicMock()
        result.exit_code = 0
        result.stdout = "/tmp/.ansible-openshell-upload-AbCdEfGh.tar.gz"
        result.stderr = ""
        return result

    fake_client.exec.side_effect = fake_exec

    with patch.object(upload_module, "AnsibleModule", lambda **kw: fake_module), patch.object(
        upload_module, "get_client", lambda module: fake_client
    ), patch.object(upload_module, "get_or_none", lambda client, name, workspace, module: fake_sandbox):
        upload_module.main()

    return fake_module, exec_calls


class TestChunkedWrite:
    def test_uses_nofollow_write_not_shell_redirect(self, mock_openshell, tmp_src):
        fake_module, exec_calls = _run_upload_main(
            {"name": "test-sandbox", "src": str(tmp_src), "dest": "/sandbox/checkout"}
        )

        # Catches a silent failure that fake_exec's always-successful
        # exit_code=0 stub could otherwise mask.
        fake_module.fail_json.assert_not_called()

        write_calls = [c for c in exec_calls if c[2] is not None]
        assert write_calls, "expected at least one chunk-write exec call"
        for _, argv, _stdin in write_calls:
            # Regression check: no shell, no `cat >>` -- a shell redirect
            # follows a symlink transparently if the target path was
            # swapped between chunks; python3 -c ... with O_NOFOLLOW does
            # not. Fails against the pre-fix ["sh", "-c", "cat >> ..."]
            # shape.
            assert argv[0] == "python3"
            assert "O_NOFOLLOW" in argv[2]
            assert argv[3] == "/tmp/.ansible-openshell-upload-AbCdEfGh.tar.gz"
