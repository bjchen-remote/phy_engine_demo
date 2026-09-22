# Independent simulation toolboxes

Toolboxes receive a text request, a task directory and resource limits. They return capability,
progress and a verified result manifest. They have no dependency on a messaging bridge or user account.

- `physics/`: the packaged macOS physics engine and its v1 task adapter.
- `registry.py`: immutable publication, atomic activation and rollback by content digest.
- `PROTOCOL.md`: the stable interface for hosts and module authors.
- `smoke.py`: standalone execution in a macOS sandbox, with a verified MP4 output.
- `tests/`: publication, protocol and result-integrity tests.

Run these commands from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s toolboxes/tests -v
python3 toolboxes/build_physics.py --check
PYTHONDONTWRITEBYTECODE=1 python3 toolboxes/smoke.py --output /tmp/toolbox-smoke
```

The packaged native code currently targets macOS arm64. Use the local builder to rebuild for a
supported Mac. The smoke executor requires macOS; it denies network access and writes only to the
current task directory. It enforces a 30-second computation timeout and a 16 MiB deliverable limit,
but does not impose a hard memory or total temporary-disk quota.

Publishing and selecting a package do not restart its host:

```sh
python3 toolboxes/registry.py --registry "$HOME/.local/share/simulation-toolboxes" \
  publish toolboxes/physics --activate
python3 toolboxes/registry.py --registry "$HOME/.local/share/simulation-toolboxes" status
python3 toolboxes/registry.py --registry "$HOME/.local/share/simulation-toolboxes" activate DIGEST
```

Use the registry configured by your host. DIGEST is the actual hash returned by publish. The host
must copy the active pointer when accepting each task and keep using that exact version for retries.
Activating a newer package affects future tasks only. Keep old versions while any job references them;
never edit a published snapshot in place. Registry publication checks integrity, not code trust: only
publish code approved by the local operator. Never package credentials or personal runtime data.
