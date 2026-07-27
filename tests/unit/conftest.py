# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import sys
from pathlib import Path

# Set up the import path so that `ansible_collections.aknochow.openshell`
# resolves to our local plugins/ directory without needing `ansible-galaxy
# collection install`.
#
# Layout expected by Ansible:
#   ansible_collections/ansible/openshell/plugins/...
#
# We create the namespace package hierarchy by inserting the project root's
# parent directories into sys.path.

_project_root = Path(__file__).resolve().parents[2]  # ansible-openshell/

# Build the namespace package path:
# <tmpdir>/ansible_collections/ansible/openshell -> <project_root>
import tempfile

_namespace_root = Path(tempfile.mkdtemp(prefix="ansible_openshell_test_"))
_ns_path = _namespace_root / "ansible_collections" / "aknochow" / "openshell"
_ns_path.parent.mkdir(parents=True, exist_ok=True)

if not _ns_path.exists():
    _ns_path.symlink_to(_project_root)

sys.path.insert(0, str(_namespace_root))
