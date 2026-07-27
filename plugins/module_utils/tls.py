# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pathlib

from ansible.module_utils.basic import AnsibleModule


def build_tls_config(module: AnsibleModule):
    """Build an openshell TlsConfig from module params.

    Returns None when no TLS params are provided (plaintext channel).
    """
    from openshell import TlsConfig

    ca = module.params.get("tls_ca")
    cert = module.params.get("tls_cert")
    key = module.params.get("tls_key")

    if not any([ca, cert, key]):
        return None

    if bool(cert) != bool(key):
        module.fail_json(msg="tls_cert and tls_key must be provided together")

    return TlsConfig(
        ca_path=pathlib.Path(ca) if ca else None,
        cert_path=pathlib.Path(cert) if cert else None,
        key_path=pathlib.Path(key) if key else None,
    )
