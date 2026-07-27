# aknochow.openshell

Ansible collection for managing [OpenShell](https://github.com/NVIDIA/OpenShell)
sandboxes — a gRPC-mediated, policy-enforced sandbox platform for running
untrusted or agentic workloads in isolation. This collection is a thin,
Ansible-native wrapper around the official `openshell` Python SDK — it
does not reimplement gateway logic, only exposes it as idempotent
Ansible modules plus one small helper script for SSH delegation.

Companion collection: [`aknochow.claude`](https://github.com/aknochow/ansible-claude)
wraps the Anthropic Messages API directly. The two compose well via
plain Ansible `delegate_to` — neither collection has any knowledge of
the other.

## Requirements

```
pip install 'openshell>=0.0.70,<0.0.91'
```

**Version pin matters.** The `openshell` SDK introduced a breaking
change around v0.0.9x: `SandboxClient.get()`/`.create()` gained a
required `workspace` keyword argument that this collection's modules do
not yet pass. Installing an unpinned/latest SDK will break `sandbox`,
`sandbox_info`, and `sandbox_exec` with
`TypeError: missing 1 required keyword-only argument: 'workspace'`.
This is a known gap (see [Known gaps](#known-gaps)), not yet fixed.

- Python ≥ 3.12 (matches the `openshell` SDK's own floor)
- `ansible-core` ≥ 2.17

## Modules

| Module | Purpose |
|---|---|
| `sandbox` | Create/delete sandboxes (state-based: `present`/`absent`), optionally attach providers, wait for ready |
| `sandbox_exec` | Execute a command in an existing sandbox, return `rc`/`stdout`/`stderr` |
| `sandbox_info` | List all sandboxes, or fetch one by name |
| `gateway_info` | Gateway health/version check |
| `provider` | Create/update/delete provider credential bundles on the gateway (uses raw gRPC stubs — the SDK doesn't wrap Provider RPCs) |

All modules share a common auth argspec (`module_utils/openshell_client.py`):

```yaml
gateway: https://openshell.apps.example.com   # required
tls_cert: /path/to/tls.crt                    # mTLS client cert
tls_key: /path/to/tls.key                     # mTLS client key
tls_ca: /path/to/ca.crt                       # custom CA (omit for system roots)
bearer_token: "{{ oidc_token }}"              # OIDC, alternative to mTLS
timeout: 30.0                                 # gRPC call timeout, default 30s
```

Every field has an `OPENSHELL_*` environment-variable fallback (see
`plugins/doc_fragments/auth.py` for the full list) so credentials can
come from the environment instead of being hardcoded in playbooks.

### `sandbox`

```yaml
- name: Create a sandbox
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    tls_cert: /certs/tls.crt
    tls_key: /certs/tls.key
    tls_ca: /certs/ca.crt
    name: my-sandbox                # optional; gateway assigns one if omitted
    image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest
    environment:
      MY_VAR: my_value
    providers:                      # attach pre-configured gateway providers
      - vertex
    wait: true                      # wait for READY phase (default true)
    wait_timeout: 300
    state: present
  register: sandbox

- name: Delete it
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    name: "{{ sandbox.sandbox.name }}"
    state: absent
```

`state: present` is idempotent by name: calling it again with the same
`name` returns `changed: false` if a matching sandbox already exists,
rather than creating a duplicate.

**Providers vs. network policy — these are separate systems.**
Attaching a provider (`providers: [vertex]`) injects a credential-rewrite
placeholder into the sandbox's environment (e.g.
`GOOGLE_VERTEX_AI_TOKEN=openshell:resolve:env:...`), but does **not**
by itself open network egress to that provider's API endpoint. The
gateway's network policy (a separate mechanism — see `openshell policy
--help` on the gateway CLI) must independently allow the relevant host,
or outbound calls from inside the sandbox will simply time out /
connection-error, regardless of whether the provider is attached. We
hit this directly: a sandbox with `providers: [vertex]` still couldn't
reach Vertex AI until a matching network policy rule for
`aiplatform.googleapis.com` was added.

**Filesystem policy overrides (`policy.filesystem`).** Sandbox images
run under a Landlock filesystem policy that can be stricter than it
looks — a path can show normal POSIX permissions (`drwxrwxrwt`) while
writes are still denied below the permission-bits level. We hit this
with `/dev/shm`: some tools (ai-guardian's gitleaks integration, for
one) hardcode writing temp files there, and the gateway's default
policy doesn't necessarily extend `/dev`'s read-write grant to that
separate tmpfs mount. Fix:

```yaml
- name: Create a sandbox with a writable /dev/shm
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest
    policy:
      filesystem:
        read_write: ["/sandbox", "/tmp", "/dev", "/dev/shm"]
        read_only: ["/usr", "/lib", "/etc"]
    state: present
```

**Important:** specifying `policy` at all REPLACES the gateway's
default policy wholesale — both filesystem *and* network — rather than
extending it. A policy with only `read_write: ["/dev/shm"]` crashes the
sandbox outright (`ContainerExited`, exit code 1) because the
container's own init process loses baseline paths it needs. Always
include the full baseline your image needs (the above matches
carbonite's own documented default: `/sandbox`, `/tmp`, `/dev`
read-write, `/usr`/`/lib`/`/etc` read-only) plus whatever you're
adding — and if the sandbox needs network access for anything (`pip
install`, downloading a release binary, etc.), that access is lost too
once any policy is set; this module doesn't yet expose
`policy.network_policies` to restore it — work around this by fetching
things on the controller and copying them in instead of granting
network egress.

### `sandbox_exec`

```yaml
- name: Run a command in a sandbox
  aknochow.openshell.sandbox_exec:
    gateway: https://openshell.apps.example.com
    tls_cert: /certs/tls.crt
    tls_key: /certs/tls.key
    tls_ca: /certs/ca.crt
    sandbox: "{{ sandbox.sandbox.id }}"
    command: [python3, -c, "print('hello')"]
    workdir: /workspace
    environment:
      FOO: bar
    stdin: "piped input"
    command_timeout: 120
  register: result
# result.rc, result.stdout, result.stderr
```

### `sandbox_info` / `gateway_info`

```yaml
- aknochow.openshell.sandbox_info:
    gateway: https://openshell.apps.example.com
    name: my-sandbox        # omit to list all sandboxes
  register: info
# info.sandboxes -> list of {id, name, phase, policy_version}

- aknochow.openshell.gateway_info:
    gateway: https://openshell.apps.example.com
  register: gw
# gw.status, gw.version, gw.healthy
```

### `provider`

```yaml
- aknochow.openshell.provider:
    gateway: https://openshell.apps.example.com
    name: vertex
    type: google-vertex-ai
    credentials:
      api_key: "{{ vault_vertex_key }}"
    state: present
```

## SSH delegation into a sandbox — `scripts/ssh_proxy.py`

Ansible's `delegate_to` lets you run any task (not just this
collection's modules) *inside* a sandbox — useful for isolating
untrusted script execution. The catch:
OpenShell has **no raw TCP SSH listener** — SSH bytes only travel
inside a gRPC `ForwardTcp` bidirectional stream. The vendor's own
`openshell` CLI solves this with a `ssh-proxy` subcommand acting as an
OpenSSH `ProxyCommand`.

`scripts/ssh_proxy.py` is a from-scratch Python reimplementation of
that relay, built directly on the SDK's `SandboxClient` and raw gRPC
stub (`CreateSshSession` + `ForwardTcp`) — **no `openshell` CLI binary
or CLI-registered gateway required.** Only the `openshell` pip package
+ `grpcio` (already collection dependencies).

```yaml
- name: Create a sandbox
  aknochow.openshell.sandbox:
    gateway: https://openshell.apps.example.com
    tls_cert: /certs/tls.crt
    tls_key: /certs/tls.key
    tls_ca: /certs/ca.crt
    image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest
    state: present
  register: sandbox

- name: Register the sandbox as an Ansible host over SSH
  ansible.builtin.add_host:
    name: sandbox_target
    ansible_host: "{{ sandbox.sandbox.name }}"
    ansible_user: sandbox
    ansible_connection: ssh
    ansible_ssh_common_args: >-
      -o ProxyCommand="python3 /path/to/aknochow.openshell/scripts/ssh_proxy.py
      --gateway https://openshell.apps.example.com
      --tls-cert /certs/tls.crt --tls-key /certs/tls.key --tls-ca /certs/ca.crt
      --sandbox {{ sandbox.sandbox.name }}"
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
      -o GlobalKnownHostsFile=/dev/null -o LogLevel=ERROR

- name: Run any task inside the sandbox
  ansible.builtin.command: whoami
  delegate_to: sandbox_target
```

Bearer-token (OIDC) auth is also supported: `--bearer-token "$TOKEN"`
instead of the three `--tls-*` flags.

**Implementation note for future maintainers:** the stdin-forwarding
side of this relay must use `os.read(fd, n)`, not
`sys.stdin.buffer.read(n)`. A buffered `.read(n)` blocks until it fills
the *entire* buffer (or EOF) — for an interactive byte stream like SSH,
small partial writes (e.g. the client's initial handshake banner) would
never get forwarded, silently deadlocking the connection. `os.read`
returns as soon as *any* data is available, which is what an
interactive relay needs. This was a real bug caught during
development — the fix is in `stdin_reader()`.

## Known gaps

- **SDK version pin required** (see [Requirements](#requirements)) —
  `sandbox`/`sandbox_info`/`sandbox_exec` don't yet pass the
  `workspace` kwarg that `openshell>=0.0.9x` requires.
- **`provider` module uses raw gRPC stubs**, not the official SDK — the
  SDK doesn't wrap Provider CRUD RPCs at all (confirmed by inspecting
  the SDK source; only `SandboxClient`'s documented methods are
  covered). If the SDK adds provider support later, this module should
  be revisited.
- **No connection plugin** — SSH delegation works via the ProxyCommand
  pattern above, not a dedicated Ansible connection plugin. A proper
  `aknochow.openshell.sandbox` connection plugin would remove the need
  to hand-construct `ansible_ssh_common_args`, but wasn't built this
  round.

## Testing

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install ansible-core 'openshell>=0.0.70,<0.0.91'
ansible-galaxy collection install . --force
python -m pytest tests/unit/
```

Live-verified against a local mTLS gateway and a remote OIDC gateway
during development (see `tests/test_local.yml`,
`tests/test_comprehensive.yml` for real end-to-end coverage of every
module, including idempotency and error-path assertions).
