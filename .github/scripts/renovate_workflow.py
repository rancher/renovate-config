#!/usr/bin/env python3
"""Render the Renovate workflows deployed to consumer repositories.

Shared by the deploy and sync workflows so both pin the same release and
replace the same obsolete workflow files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

PRESET_REPO = "rancher/renovate-config"
WORKFLOW_PATH = ".github/workflows/renovate-vault.yml"
OBSOLETE_WORKFLOWS = (".github/workflows/renovate.yml",)
APPROVE_PATH = ".github/workflows/auto-approve-bot-prs.yml"
APPROVE_PLACEHOLDER = "OWNER--REPOSITORY"
# Used when no release tag can be read, e.g. without API access.
FALLBACK_RELEASE = ("f62bbe9fec9bef07a9f15addc2f9c6ea278ac736", "v1.0.0")

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PIN_RE = re.compile(r"(/\.github/workflows/renovate-vault\.yml)@(?:release|[0-9a-f]{40})(?: # .*)?$", re.MULTILINE)
ZIZMOR_IGNORE_RE = re.compile(r" # zizmor: ignore\[unpinned-uses\].*$", re.MULTILINE)
TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def latest_release(get: Callable[[str], object]) -> tuple[str, str]:
    """Commit SHA and name of the highest `vX.Y.Z` tag of rancher/renovate-config."""
    tags = get(f"repos/{PRESET_REPO}/tags?per_page=100") or []
    versions = [(tuple(map(int, m.groups())), t) for t in tags if isinstance(t, dict) and (m := TAG_RE.match(t.get("name", "")))]
    if not versions:
        return FALLBACK_RELEASE
    tag = max(versions, key=lambda item: item[0])[1]
    return tag["commit"]["sha"], tag["name"]


def render_renovate_workflow(template: str, sha: str, tag: str) -> str:
    """Pin the reusable workflow to the release commit, as consumers must not use `@release`."""
    text = TRAILING_SPACE_RE.sub("", template)
    text = ZIZMOR_IGNORE_RE.sub("", text)
    return PIN_RE.sub(lambda m: f"{m.group(1)}@{sha} # {tag}", text)


def render_auto_approve(template: str, repo: str) -> str:
    owner, name = repo.split("/", 1)
    return template.replace(APPROVE_PLACEHOLDER, f"{owner}--{name}")


def api_get(path: str) -> object:
    req = urllib.request.Request(f"https://api.github.com/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    if token := os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, ValueError) as err:
        print(f"Cannot read {path}: {err}", file=sys.stderr)
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", required=True, help="files/renovate-vault.yml")
    parser.add_argument("--repo-dir", required=True, help="checkout of the consumer repository")
    args = parser.parse_args(argv)
    sha, tag = latest_release(api_get)
    repo_dir = Path(args.repo_dir)
    target = repo_dir / WORKFLOW_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_renovate_workflow(Path(args.template).read_text(), sha, tag))
    # Print obsolete workflows for the caller to delete.
    for path in OBSOLETE_WORKFLOWS:
        if (repo_dir / path).is_file():
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
