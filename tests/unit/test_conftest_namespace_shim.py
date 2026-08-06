# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import atexit

from conftest import _create_namespace_shim


def test_namespace_shim_registers_working_cleanup(monkeypatch, tmp_path):
    registered = []
    monkeypatch.setattr(
        atexit, "register", lambda fn, *args, **kwargs: registered.append((fn, args, kwargs))
    )

    result = _create_namespace_shim("test_shim_", "openshell", tmp_path)

    assert result.exists()
    assert len(registered) == 1
    fn, args, kwargs = registered[0]

    # Regression check: it's not enough that *something* was registered --
    # invoking exactly what was registered must actually remove the
    # directory. This is what catches a wrong path, wrong function, or a
    # missing atexit.register call entirely (all of which the previous
    # code had -- no cleanup was registered at all).
    fn(*args, **kwargs)
    assert not result.exists()
