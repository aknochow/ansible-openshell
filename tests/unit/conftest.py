# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Set up the import path so that `ansible_collections.aknochow.openshell`
# resolves to our local plugins/ directory without needing `ansible-galaxy
# collection install`.
#
# Layout expected by Ansible:
#   ansible_collections/aknochow/openshell/plugins/...
#
# We create the namespace package hierarchy by inserting the project root's
# parent directories into sys.path.

_project_root = Path(__file__).resolve().parents[2]  # ansible-openshell/


def _create_namespace_shim(prefix: str, collection_name: str, project_root: Path) -> Path:
    """Create a temp namespace-package dir symlinking to project_root.

    Registers cleanup via atexit so the temp directory doesn't leak into
    /tmp on every test run -- returns the created root so callers (and
    tests) can inspect or exercise it directly.
    """
    namespace_root = Path(tempfile.mkdtemp(prefix=prefix))
    ns_path = namespace_root / "ansible_collections" / "aknochow" / collection_name
    ns_path.parent.mkdir(parents=True, exist_ok=True)

    if not ns_path.exists():
        ns_path.symlink_to(project_root)

    atexit.register(shutil.rmtree, str(namespace_root), ignore_errors=True)
    return namespace_root


# Build the namespace package path:
# <tmpdir>/ansible_collections/aknochow/openshell -> <project_root>
_namespace_root = _create_namespace_shim("ansible_openshell_test_", "openshell", _project_root)

sys.path.insert(0, str(_namespace_root))
