# ansible-openshell: Project Context

## Read first

- [CONTRIBUTING.md](CONTRIBUTING.md): branch names, commits, test and lint commands
- [README.md](README.md): project overview

## What this repo is

Ansible collection for managing OpenShell sandboxes: a thin, Ansible-native wrapper around the official `openshell` Python SDK. It exposes gateway operations as idempotent Ansible modules plus one helper script for SSH delegation.

## Commands

Test and sanity commands, copied from [CONTRIBUTING.md](CONTRIBUTING.md):

### Quick test run:
```bash
uv run pytest
```

### Syncing dependencies:
```bash
uv sync --extra dev
uv run pytest -v
```

### Running sanity tests:
Ansible sanity tests require the repository to be within an `ansible_collections/aknochow/openshell` directory hierarchy:
```bash
uv run ansible-test sanity --local --python 3.13 -v
```
