#!/usr/bin/env python3
"""Contract tests: mode=bootstrap must succeed on a freshly installed host.

Bootstrap skips the docker role (effective_run_main_stack is false), so on a clean OS
there is no docker CLI and no `docker` group. Two things broke dh-germ-1 after a
reinstall on 2026-09-22:
  * monitoring_agent ran in bootstrap and died on "Cannot find docker CLI in path",
    aborting the play before user_shell created the deploy user;
  * user_shell added the deploy user to the `docker` group, which did not exist yet
    ("Group docker does not exist").
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def role_entry(roles: list, name: str) -> dict:
    for entry in roles:
        if isinstance(entry, dict) and entry.get("role") == name:
            return entry
    fail(f"role {name} not found in playbook.yml")
    raise AssertionError  # unreachable


def main() -> None:
    play = yaml.safe_load((ROOT / "playbook.yml").read_text())[0]
    roles = play["roles"]

    for name in ("monitoring_agent", "monitoring_stack"):
        when = str(role_entry(roles, name).get("when", ""))
        if "effective_run_main_stack" not in when:
            fail(f"{name} must be gated by effective_run_main_stack (it needs docker, which bootstrap skips)")
        print(f"PASS: {name} does not run in bootstrap/lockdown")

    tasks = yaml.safe_load((ROOT / "roles/user_shell/tasks/main.yml").read_text())
    names = [t.get("name", "") for t in tasks]
    create_idx = names.index("Create deployment user")
    group_tasks = [
        (i, t)
        for i, t in enumerate(tasks)
        if "ansible.builtin.group" in t and "new_user_groups" in str(t.get("loop", ""))
    ]
    if not group_tasks:
        fail("user_shell must ensure every group in new_user_groups exists before creating the user")
    idx, task = group_tasks[0]
    if idx > create_idx:
        fail("the group-ensure task must run before 'Create deployment user'")
    if "user" not in task.get("tags", []):
        fail("the group-ensure task must carry the `user` tag so `--tags user` bootstraps work")
    if "create_user" not in str(task.get("when", "")):
        fail("the group-ensure task must be gated by create_user")
    print("PASS: user_shell creates missing supplementary groups before the deploy user")

    print("All bootstrap fresh-host contract tests passed.")


if __name__ == "__main__":
    main()
