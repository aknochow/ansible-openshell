#!/usr/bin/env python3
"""SSH ProxyCommand relay for OpenShell sandboxes — SDK-only, no CLI.

OpenShell has no raw TCP SSH listener: SSH bytes only travel inside a
gRPC ForwardTcp bidirectional stream. This script is a Python
implementation of that relay (equivalent to the vendor's `openshell
ssh-proxy` CLI subcommand), built directly on the `openshell` SDK's
SandboxClient and raw gRPC stub — no `openshell` CLI binary or
CLI-registered gateway required.

Usage (as an OpenSSH ProxyCommand):

  ssh -o ProxyCommand="python3 ssh_proxy.py \\
        --gateway https://openshell.apps.example.com \\
        --tls-cert /certs/tls.crt --tls-key /certs/tls.key --tls-ca /certs/ca.crt \\
        --sandbox my-sandbox" \\
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
      sandbox@my-sandbox

Or with bearer-token (OIDC) auth instead of mTLS:

  python3 ssh_proxy.py --gateway https://gw --bearer-token "$TOKEN" --sandbox my-sandbox

The token can also come from the OPENSHELL_BEARER_TOKEN env var instead
of --bearer-token -- preferred when the caller (e.g. an Ansible
ansible_ssh_common_args ProxyCommand) would otherwise expose it via
/proc/*/cmdline as a plain CLI argument:

  OPENSHELL_BEARER_TOKEN="$TOKEN" python3 ssh_proxy.py --gateway https://gw --sandbox my-sandbox
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading

CHUNK_SIZE = 32768


def build_client(args):
    import pathlib

    from openshell import SandboxClient, TlsConfig
    from urllib.parse import urlparse

    parsed = urlparse(args.gateway)
    host = parsed.hostname or args.gateway
    if parsed.port:
        endpoint = f"{host}:{parsed.port}"
    elif parsed.scheme == "http":
        endpoint = f"{host}:80"
    else:
        endpoint = f"{host}:443"

    tls = None
    if args.tls_cert or args.tls_key or args.tls_ca:
        tls = TlsConfig(
            ca_path=pathlib.Path(args.tls_ca) if args.tls_ca else None,
            cert_path=pathlib.Path(args.tls_cert) if args.tls_cert else None,
            key_path=pathlib.Path(args.tls_key) if args.tls_key else None,
        )
    elif parsed.scheme == "https":
        from openshell import TlsConfig as _TlsConfig

        tls = _TlsConfig()

    return SandboxClient(
        endpoint=endpoint,
        tls=tls,
        bearer_token=args.bearer_token,
        timeout=args.timeout,
    )


def stdin_reader(out_queue: "queue.Queue", init_frame):
    """Feed the init frame, then relay stdin bytes as data frames.

    Uses os.read() rather than a buffered .read(n) — for an interactive
    byte stream like SSH, a partial chunk must be forwarded immediately.
    BufferedReader.read(n) blocks until it fills the full n bytes (or
    EOF), which never happens for small interactive writes and would
    deadlock the SSH handshake.
    """
    import os

    out_queue.put(init_frame)
    fd = sys.stdin.buffer.fileno()
    while True:
        chunk = os.read(fd, CHUNK_SIZE)
        if not chunk:
            break
        out_queue.put(chunk)
    out_queue.put(None)  # sentinel: stop the frame generator


def frame_generator(in_queue: "queue.Queue", frame_cls):
    while True:
        item = in_queue.get()
        if item is None:
            return
        if isinstance(item, bytes):
            yield frame_cls(data=item)
        else:
            yield item  # already a TcpForwardFrame (the init frame)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True, help="Gateway URL, e.g. https://openshell.apps.example.com")
    parser.add_argument("--tls-cert", default=None)
    parser.add_argument("--tls-key", default=None)
    parser.add_argument("--tls-ca", default=None)
    parser.add_argument(
        "--bearer-token",
        default=None,
        help="Falls back to the OPENSHELL_BEARER_TOKEN env var if not given "
        "(same var name aknochow.openshell modules already fall back to) -- "
        "avoids the token being visible via /proc/*/cmdline as a CLI arg.",
    )
    parser.add_argument("--sandbox", required=True, help="Sandbox name")
    parser.add_argument(
        "--workspace",
        default="",
        help="Workspace the sandbox belongs to (openshell>=0.0.88; empty string works against gateways without workspace support)",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.bearer_token is None:
        import os

        args.bearer_token = os.environ.get("OPENSHELL_BEARER_TOKEN")

    from openshell._proto import openshell_pb2, openshell_pb2_grpc

    client = build_client(args)
    try:
        sandbox = client.get(args.sandbox, workspace=args.workspace)
        # SandboxClient has no public wrapper for CreateSshSession/ForwardTcp
        # (unlike CreateSandbox, which client.create() covers) — reaching
        # into client._channel is the only way to reach these RPCs today.
        # Known limitation, not fixable without an SDK change; pin the
        # openshell version constraint in setup.py/galaxy.yml if this ever
        # needs to track a channel-shape change upstream.
        stub = openshell_pb2_grpc.OpenShellStub(client._channel)

        session = stub.CreateSshSession(
            openshell_pb2.CreateSshSessionRequest(sandbox_id=sandbox.id)
        )

        init_frame = openshell_pb2.TcpForwardFrame(
            init=openshell_pb2.TcpForwardInit(
                sandbox_id=sandbox.id,
                service_id=f"ssh-proxy:{sandbox.id}",
                ssh=openshell_pb2.SshRelayTarget(),
                authorization_token=session.token,
            )
        )

        out_queue: "queue.Queue" = queue.Queue()
        reader = threading.Thread(
            target=stdin_reader, args=(out_queue, init_frame), daemon=True
        )
        reader.start()

        response_iterator = stub.ForwardTcp(
            frame_generator(out_queue, openshell_pb2.TcpForwardFrame)
        )

        stdout = sys.stdout.buffer
        for frame in response_iterator:
            if frame.HasField("data") and frame.data:
                stdout.write(frame.data)
                stdout.flush()
    finally:
        client.close()


if __name__ == "__main__":
    main()
