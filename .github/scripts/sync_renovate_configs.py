#!/usr/bin/env python3
"""Sync consumer `.github/renovate.json` files with rancher/renovate-config presets.

Removes EOL Rancher presets, adds newly supported ones, keeps preset refs
consistent, adds the root `default` preset, optionally populates minimal
configs with Rancher release branch presets, replaces the Renovate
workflow with the current template and enables automerge where the
repository settings make it safe. Changes are proposed as pull
requests; a Markdown report is written for every scanned repository.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from renovate_workflow import (
    APPROVE_PATH,
    OBSOLETE_WORKFLOWS,
    PRESET_REPO,
    WORKFLOW_PATH,
    latest_release,
    render_auto_approve,
    render_renovate_workflow,
)

RANCHER_REPO = "rancher/rancher"
CONFIG_PATH = ".github/renovate.json"
SYNC_BRANCH = "renovate-config-sync"
PR_TITLE = "Update Renovate configuration"
PRESET_REFS = ("main", "release")
DEFAULT_REF = "release"
# GitHub rejects job summaries above 1 MiB.
SUMMARY_LIMIT = 1000 * 1024
# GitHub throttles and fails backend requests transiently; repeat those.
TRANSIENT_STATUS = (429, 500, 502, 503, 504)
RETRIES = 3
RETRY_DELAY = 2.0

REF_RE = re.compile(r"^github>rancher/renovate-config(?:(//|:)([^#]+))?(?:#(.+))?$")
BRANCH_RE = re.compile(r"^(?P<prefix>(?:.*[^\d.])?)(?P<major>\d+)\.(?P<minor>\d+)$")
RANCHER_PRESET_RE = re.compile(r"^rancher-2\.(\d+)$")
# Prefixes whose `2.Y` branches follow Rancher versions directly.
IDENTITY_PREFIXES = ("release/v", "release-v", "dev-v", "v")
PREFIX_PREFERENCE = ("release/v", "release-v", "release/", "release-", "dev-v", "v")
PRESET_RULE_KEYS = {"description", "matchBaseBranches", "extends"}
AUTO_MERGE_QUERY = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { autoMergeAllowed } }"


# --- Preset references -----------------------------------------------------


@dataclass
class PresetRef:
    sep: str
    preset: str | None
    ref: str | None

    @classmethod
    def parse(cls, value: object) -> PresetRef | None:
        if not isinstance(value, str):
            return None
        match = REF_RE.match(value)
        if not match:
            return None
        return cls(match.group(1) or "//", match.group(2), match.group(3))

    @property
    def name(self) -> str:
        return self.preset or "default"

    @property
    def rancher_minor(self) -> int | None:
        return rancher_minor(self.name)

    def render(self) -> str:
        base = "github>rancher/renovate-config"
        if self.preset:
            base += f"{self.sep}{self.preset}"
        return f"{base}#{self.ref}" if self.ref else base


def rancher_minor(name: str) -> int | None:
    match = RANCHER_PRESET_RE.match(name)
    return int(match.group(1)) if match else None


def preset_ref(name: str, ref: str, sep: str = "//") -> str:
    return PresetRef(sep, name, ref).render()


def parse_refs(items: object) -> list[PresetRef]:
    return [r for r in map(PresetRef.parse, items if isinstance(items, list) else []) if r]


def rules_of(cfg: dict) -> list[dict]:
    return [r for r in cfg.get("packageRules") or [] if isinstance(r, dict)]


def extends_owners(cfg: dict) -> list[dict]:
    return [cfg] + rules_of(cfg)


def extends_lists(cfg: dict) -> list[list]:
    return [o["extends"] for o in extends_owners(cfg) if isinstance(o.get("extends"), list)]


def all_refs(cfg: dict) -> list[PresetRef]:
    return [r for items in extends_lists(cfg) for r in parse_refs(items)]


def rancher_refs(refs: list[PresetRef]) -> list[PresetRef]:
    return [r for r in refs if r.name == "rancher-main" or r.rancher_minor is not None]


# --- Branches ----------------------------------------------------------------


@dataclass(frozen=True)
class Branch:
    prefix: str
    major: int
    minor: int

    @classmethod
    def parse(cls, name: str) -> Branch | None:
        match = BRANCH_RE.match(name)
        if not match:
            return None
        return cls(match.group("prefix"), int(match.group("major")), int(match.group("minor")))

    @property
    def family(self) -> tuple[str, int]:
        return (self.prefix, self.major)

    def with_minor(self, minor: int) -> str:
        return f"{self.prefix}{self.major}.{minor}"


def is_exact_branch(name: object) -> bool:
    if not isinstance(name, str):
        return False
    return not (name.startswith("/") and name.endswith("/")) and not any(c in name for c in "*?{}[]!")


def branch_matches(pattern: str, branch: str) -> bool:
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        try:
            return re.search(pattern[1:-1].replace("\\/", "/"), branch) is not None
        except re.error:
            return False
    return pattern == branch


@dataclass
class Mapping:
    """Maps Rancher minor versions (2.Y) to repository branches."""

    source: str
    explicit: dict[int, str] = field(default_factory=dict)
    family: tuple[str, int] | None = None
    offset: int | None = None
    # Rancher minor -> component (major, minor) versions pinned by rancher/rancher.
    pinned: dict[int, set[tuple[int, int]]] = field(default_factory=dict)

    def branch_for(self, minor: int) -> str | None:
        if minor in self.explicit:
            return self.explicit[minor]
        if self.family is None or self.offset is None or minor - self.offset < 0:
            return None
        guess = Branch(*self.family, 0).with_minor(minor - self.offset)
        # Never extrapolate past what rancher/rancher actually pins.
        if minor in self.pinned and (self.family[1], minor - self.offset) not in self.pinned[minor]:
            return None
        return guess

    def rancher_for(self, branch: str) -> int | None:
        for minor, name in self.explicit.items():
            if name == branch:
                return minor
        parsed = Branch.parse(branch)
        if parsed and self.family == parsed.family and self.offset is not None:
            return parsed.minor + self.offset
        return None


def mapping_from_pairs(pairs: list[tuple[str, int]], source: str) -> tuple[Mapping | None, str | None]:
    """Build a mapping from (branch, Rancher minor) pairs; returns (mapping, conflict)."""
    parsed = [(p, y, b) for b, y in pairs if (p := Branch.parse(b))]
    if not parsed:
        return None, None
    family = max(parsed, key=lambda item: item[1])[0].family
    offsets = {y - p.minor for p, y, _ in parsed if p.family == family}
    explicit = {y: b for _, y, b in parsed}
    if len(offsets) == 1:
        return Mapping(source, explicit, family, offsets.pop()), None
    branches = ", ".join(f"`{b}`" for _, _, b in sorted(parsed, key=lambda item: item[1]))
    return Mapping(source, explicit), f"branches do not follow one version offset ({branches})"


def identity_mapping(branches: set[str], supported: list[int]) -> Mapping | None:
    prefixes = {
        p.prefix
        for p in map(Branch.parse, branches)
        if p and p.major == 2 and p.prefix in IDENTITY_PREFIXES and p.minor in supported
    }
    if not prefixes:
        return None
    prefix = min(prefixes, key=IDENTITY_PREFIXES.index)
    return Mapping("Rancher version branch names", family=(prefix, 2), offset=0)


def prefix_rank(prefix: str) -> int:
    return PREFIX_PREFERENCE.index(prefix) if prefix in PREFIX_PREFERENCE else len(PREFIX_PREFERENCE)


def oracle_mapping(entries: list[tuple[int, int, int]], branches: set[str]) -> tuple[Mapping | None, str | None]:
    """Map oracle (major, minor, Rancher minor) entries onto existing branches."""
    by_version: dict[tuple[int, int], set[int]] = {}
    for major, minor, rancher in entries:
        by_version.setdefault((major, minor), set()).add(rancher)
    parsed = [p for p in map(Branch.parse, branches) if p]
    pairs = []
    for (major, minor), ranchers in sorted(by_version.items()):
        candidates = [p for p in parsed if p.major == major and p.minor == minor]
        # A component minor shipped with several Rancher versions has no single preset.
        if len(ranchers) != 1 or not candidates:
            continue
        best = min(candidates, key=lambda p: prefix_rank(p.prefix))
        pairs.append((best.with_minor(minor), ranchers.pop()))
    mapping, conflict = mapping_from_pairs(pairs, "component versions pinned in rancher/rancher")
    if mapping:
        for major, minor, rancher in entries:
            mapping.pinned.setdefault(rancher, set()).add((major, minor))
    return mapping, conflict


# --- Preset catalog ----------------------------------------------------------


def is_eol_preset(content: dict | None) -> bool:
    """A preset is EOL when it has rules and all of them are disabled."""
    rules = (content or {}).get("packageRules") or []
    return bool(rules) and all(isinstance(r, dict) and r.get("enabled") is False for r in rules)


@dataclass
class PresetCatalog:
    """Preset files per renovate-config ref; Rancher presets keep their content."""

    files: dict[str, dict[str, dict | None]]

    def status(self, ref: str, name: str) -> str:
        # EOL lands on main before release promotion; honour it for every ref.
        if rancher_minor(name) is not None and name in self.files.get(ref, {}) and any(
            self._status(r, name) == "eol" for r in self.files if r != ref and self.files[r]
        ):
            return "eol"
        return self._status(ref, name)

    def _status(self, ref: str, name: str) -> str:
        files = self.files.get(ref, {})
        minor = rancher_minor(name)
        if name in files:
            return "eol" if minor is not None and is_eol_preset(files[name]) else "ok"
        if minor is None:
            return "missing"
        known = [m for m in map(rancher_minor, files) if m is not None]
        # Presets newer than every published one are not promoted yet, not EOL.
        return "unreleased" if known and minor > max(known) else "eol"

    def supported(self, ref: str) -> list[int]:
        minors = (rancher_minor(n) for n in self.files.get(ref, {}))
        return sorted(m for m in minors if m is not None and self.status(ref, f"rancher-2.{m}") == "ok")


# --- Rancher component oracle ------------------------------------------------


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_candidates(key: str) -> set[str]:
    names = {normalize_name(key)}
    for prefix in ("default", "cattle", "rancher"):
        names |= {n[len(prefix):] for n in list(names) if n.startswith(prefix) and len(n) > len(prefix)}
    names |= {n[: -len("agent")] for n in list(names) if n.endswith("agent") and len(n) > len("agent")}
    return names


def parse_pins(go_mod: str, build_yaml: str, dockerfile: str) -> dict[str, tuple[int, int]]:
    """Return normalized component name -> (major, minor); earlier sources win."""
    pins: dict[str, tuple[int, int]] = {}

    def add(names: set[str], major: str, minor: str) -> None:
        for name in names:
            pins.setdefault(name, (int(major), int(minor)))

    for m in re.finditer(r"^(\w+?)Version:\s*\S*?(?:\+up|:v)(\d+)\.(\d+)\.\d+", build_yaml, re.M):
        add(name_candidates(m.group(1)), m.group(2), m.group(3))
    for m in re.finditer(r"^ENV CATTLE_(\w+?)_VERSION=v(\d+)\.(\d+)\.\d+", dockerfile, re.M):
        add(name_candidates(m.group(1)), m.group(2), m.group(3))
    for text in (build_yaml, dockerfile):
        for m in re.finditer(r"\brancher/([\w.-]+):v(\d+)\.(\d+)\.\d+", text):
            add({normalize_name(m.group(1))}, m.group(2), m.group(3))
    modules: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r"^\s*(?:require\s+)?github\.com/rancher/([\w.-]+)\S*\s+v(\d+)\.(\d+)\.(\S+)(.*)$", go_mod, re.M):
        if "indirect" in m.group(5) or re.search(r"\d{14}-[0-9a-f]{12}", m.group(4)):
            continue
        name, version = normalize_name(m.group(1)), (int(m.group(2)), int(m.group(3)))
        modules[name] = max(modules.get(name, version), version)
    for name, version in modules.items():
        pins.setdefault(name, version)
    return pins


# --- GitHub API --------------------------------------------------------------


class GitHub:
    def __init__(self, token: str, api: str = "https://api.github.com") -> None:
        self.token = token
        self.api = api.rstrip("/")
        self._branches: dict[str, set[str]] = {}
        self._files: dict[tuple[str, str, str | None], str | None] = {}

    def request(self, method: str, path: str, body: object = None, raw: bool = False, allow_missing: bool = False):
        url = f"{self.api}/{path.lstrip('/')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        # Only requests that are safe to repeat are retried. Git objects are
        # content addressed, GraphQL is used for reads, and DELETE is made
        # idempotent by its callers. Creating a PR or ref is not repeated: a
        # response lost after the server created it would surface as a conflict.
        retry = method in ("GET", "HEAD", "PATCH", "DELETE") or path == "graphql" or path.endswith(("/git/commits", "/git/trees"))
        for attempt in range(1, RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = resp.read().decode()
                break
            except urllib.error.HTTPError as err:
                if allow_missing and err.code in (403, 404, 409):
                    return None
                if retry and err.code in TRANSIENT_STATUS and attempt < RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
                    continue
                detail = err.read().decode(errors="replace")[:300]
                if err.code == 403 and "rate limit" not in detail:
                    detail = f"{detail} (the token may not be scoped to this repository)"
                elif err.code == 422 and method == "POST" and path.endswith("/git/refs"):
                    detail = f"{detail} (a branch ruleset may forbid creating the branch)"
                raise RuntimeError(f"{method} {path}: HTTP {err.code} {detail}") from err
        if raw:
            return payload
        return json.loads(payload) if payload else None

    def graphql(self, query: str, **variables) -> dict:
        result = self.request("POST", "graphql", {"query": query, "variables": variables})
        if result.get("errors"):
            raise RuntimeError(f"GraphQL: {result['errors'][0].get('message')}")
        return result["data"]

    def paginate(self, path: str) -> list:
        items, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            batch = self.request("GET", f"{path}{sep}per_page=100&page={page}") or []
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def file(self, repo: str, path: str, ref: str | None = None) -> str | None:
        key = (repo, path, ref)
        if key not in self._files:
            query = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
            self._files[key] = self.request("GET", f"repos/{repo}/contents/{path}{query}", raw=True, allow_missing=True)
        return self._files[key]

    def branches(self, repo: str) -> set[str]:
        if repo not in self._branches:
            self._branches[repo] = {b["name"] for b in self.paginate(f"repos/{repo}/branches")}
        return self._branches[repo]


def load_catalog(gh: GitHub) -> PresetCatalog:
    files: dict[str, dict[str, dict | None]] = {}
    for ref in PRESET_REFS:
        tree = gh.request("GET", f"repos/{PRESET_REPO}/git/trees/{ref}")
        files[ref] = {}
        for entry in tree.get("tree", []):
            path = entry["path"]
            if entry["type"] != "blob" or not path.endswith(".json"):
                continue
            name = path[: -len(".json")]
            content = json.loads(gh.file(PRESET_REPO, path, ref) or "{}") if rancher_minor(name) is not None else None
            files[ref][name] = content
    return PresetCatalog(files)


def load_oracle(gh: GitHub, minors: set[int]) -> dict[str, list[tuple[int, int, int]]]:
    """Component name -> [(major, minor, Rancher minor)] from rancher/rancher branches."""
    rancher_branches = gh.branches(RANCHER_REPO)
    refs = {f"release/v2.{m}": m for m in minors if f"release/v2.{m}" in rancher_branches}
    match = re.search(r"^chartDefaultBranch:\s*dev-v2\.(\d+)", gh.file(RANCHER_REPO, "build.yaml", "main") or "", re.M)
    if match:
        refs["main"] = int(match.group(1))
    oracle: dict[str, list[tuple[int, int, int]]] = {}
    for ref, rancher in refs.items():
        pins = parse_pins(*(gh.file(RANCHER_REPO, f, ref) or "" for f in ("go.mod", "build.yaml", "package/Dockerfile")))
        for name, (major, minor) in pins.items():
            oracle.setdefault(name, []).append((major, minor, rancher))
    return oracle


# --- Config transformation ---------------------------------------------------


@dataclass
class RepoContext:
    repo: str
    default_branch: str
    branches: set[str]
    catalog: PresetCatalog
    oracle: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)
    template_description: str | None = None
    populate: bool = True


@dataclass
class SyncResult:
    config: dict
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ref_label: str = ""


def preset_rules(cfg: dict) -> list[tuple[dict, str, PresetRef]]:
    """Rules applying one Rancher preset to one exact branch."""
    found = []
    for rule in rules_of(cfg):
        branches = rule.get("matchBaseBranches")
        if not (isinstance(branches, list) and len(branches) == 1 and is_exact_branch(branches[0])):
            continue
        refs = rancher_refs(parse_refs(rule.get("extends")))
        if len(refs) == 1:
            found.append((rule, branches[0], refs[0]))
    return found


def is_preset_only_rule(rule: dict) -> bool:
    extends = rule.get("extends") or []
    return set(rule) <= PRESET_RULE_KEYS and len(parse_refs(extends)) == len(extends)


def is_empty_rule(rule: object) -> bool:
    return isinstance(rule, dict) and not rule.get("extends") and set(rule) <= PRESET_RULE_KEYS


def prune_rules(cfg: dict) -> None:
    """Drop rules left without presets and empty `extends` lists."""
    cfg["packageRules"] = [r for r in cfg["packageRules"] if not is_empty_rule(r)]
    for owner in extends_owners(cfg):
        if owner.get("extends") == []:
            del owner["extends"]


def with_key_after(cfg: dict, key: str, value: object, after: str) -> dict:
    """Set key, inserting it after `after` (or first) when missing."""
    if key in cfg:
        cfg[key] = value
        return cfg
    result = {} if after in cfg else {key: value}
    for k, v in cfg.items():
        result[k] = v
        if k == after:
            result[key] = value
    return result


def insert_before(items: list, value: str, before: str) -> None:
    if value not in items:
        items.insert(items.index(before) if before in items else len(items), value)


def normalize_refs(cfg: dict, result: SyncResult) -> str:
    """Use one preset ref everywhere: keep a single `#main` or `#release`, else switch to `#release`."""
    refs = {r.ref for r in all_refs(cfg)}
    if not refs:
        result.ref_label = "none"
        return DEFAULT_REF
    if len(refs) == 1 and next(iter(refs)) in PRESET_REFS:
        result.ref_label = next(iter(refs))
        return result.ref_label
    shown = ", ".join(f"`#{r}`" if r else "no ref" for r in sorted(refs, key=str))
    result.ref_label = f"mixed -> {DEFAULT_REF}"
    for items in extends_lists(cfg):
        for i, value in enumerate(items):
            if parsed := PresetRef.parse(value):
                parsed.ref = DEFAULT_REF
                items[i] = parsed.render()
    result.changes.append(f"Use `#{DEFAULT_REF}` for every rancher/renovate-config preset (was {shown}).")
    return DEFAULT_REF


def rename_base_branches(cfg: dict, result: SyncResult) -> dict:
    """Rename the deprecated `baseBranches` key, keeping an existing `baseBranchPatterns`."""
    if "baseBranches" not in cfg or "baseBranchPatterns" in cfg:
        return cfg
    result.changes.append("Rename deprecated `baseBranches` to `baseBranchPatterns`.")
    return {("baseBranchPatterns" if k == "baseBranches" else k): v for k, v in cfg.items()}


def ensure_root_default(cfg: dict, ref: str, result: SyncResult) -> dict:
    """Extend the `default` preset at top level and dedupe it out of branch rules."""
    root = cfg["extends"] if isinstance(cfg.get("extends"), list) else []
    has_default = any(r.name == "default" for r in parse_refs(root))
    moved = False
    for rule in rules_of(cfg):
        extends = rule.get("extends")
        if not isinstance(extends, list):
            continue
        kept = [e for e in extends if not ((p := PresetRef.parse(e)) and p.name == "default")]
        if len(kept) != len(extends):
            moved = True
            rule["extends"] = kept
    if moved:
        prune_rules(cfg)
    if has_default:
        if moved:
            result.changes.append("Remove the `default` preset from branch rules; it is already extended at top level.")
        explicit = preset_ref("default", ref)
        for i, value in enumerate(root):
            if (p := PresetRef.parse(value)) and p.name == "default" and value != explicit:
                root[i] = explicit
                result.changes.append(f"Reference the `default` preset as `{explicit}`.")
        return cfg
    result.changes.append(
        "Move the `default` preset from branch rules to top-level `extends`."
        if moved
        else "Extend the `default` preset at top level so shared managers are loaded once."
    )
    return with_key_after(cfg, "extends", [preset_ref("default", ref)] + root, "$schema")


def resolve_default_branch(cfg: dict, default_branch: str, result: SyncResult) -> None:
    """Replace the `$default` placeholder with the repository default branch."""
    lists = [cfg.get("baseBranchPatterns")] + [r.get("matchBaseBranches") for r in rules_of(cfg)]
    changed = False
    for items in lists:
        if isinstance(items, list) and "$default" in items:
            items[:] = list(dict.fromkeys(default_branch if b == "$default" else b for b in items))
            changed = True
    if changed:
        result.changes.append(f"Replace `$default` with the default branch `{default_branch}`.")


def remove_branches(cfg: dict, branches: set[str], result: SyncResult) -> None:
    if isinstance(cfg.get("baseBranchPatterns"), list):
        cfg["baseBranchPatterns"] = [b for b in cfg["baseBranchPatterns"] if b not in branches]
    rules = []
    for rule in cfg.get("packageRules") or []:
        match = rule.get("matchBaseBranches") if isinstance(rule, dict) else None
        if isinstance(match, list) and any(b in branches for b in match):
            rule["matchBaseBranches"] = [b for b in match if b not in branches]
            if not rule["matchBaseBranches"]:
                # An empty matchBaseBranches would apply the rule to every branch.
                if not is_preset_only_rule(rule):
                    label = rule.get("description") or json.dumps({k: v for k, v in rule.items() if k != "matchBaseBranches"})
                    result.changes.append(f"Remove custom rule that only applied to removed branches: {label}")
                continue
        rules.append(rule)
    cfg["packageRules"] = rules


def remove_eol(cfg: dict, ref: str, ctx: RepoContext, result: SyncResult) -> None:
    """Drop EOL presets, and the branches referenced only by them, from every `extends` and branch list.

    A branch left without any Rancher preset is removed unless it is the default
    branch, which is reported instead.
    """
    exact = {id(rule): branch for rule, branch, _ in preset_rules(cfg)}
    removed: dict[str, list[str]] = {}
    eol_branches = set()
    for owner in extends_owners(cfg):
        items = owner.get("extends")
        if not isinstance(items, list):
            continue
        branch = exact.get(id(owner))
        kept = []
        for value in items:
            parsed = PresetRef.parse(value)
            if parsed and parsed.rancher_minor is not None and ctx.catalog.status(ref, parsed.name) == "eol":
                removed.setdefault(parsed.name, []).extend([branch] if branch else [])
            else:
                kept.append(value)
        if len(kept) == len(items):
            continue
        owner["extends"] = kept
        if branch and not rancher_refs(parse_refs(kept)):
            if branch == ctx.default_branch:
                result.warnings.append(f"Default branch `{branch}` used an EOL preset; branch kept, review manually.")
            else:
                eol_branches.add(branch)
    if not removed:
        return
    for name, branches in sorted(removed.items(), key=lambda item: rancher_minor(item[0])):
        where = f" and branch {', '.join(f'`{b}`' for b in branches)}" if branches else ""
        result.changes.append(f"Remove EOL preset `{name}`{where}.")
    remove_branches(cfg, eol_branches, result)
    prune_rules(cfg)


def add_branch_to_lists(cfg: dict, new_branch: str, sibling: str) -> None:
    if isinstance(cfg.get("baseBranchPatterns"), list):
        insert_before(cfg["baseBranchPatterns"], new_branch, sibling)
    for rule in rules_of(cfg):
        match = rule.get("matchBaseBranches")
        if isinstance(match, list) and len(match) >= 2 and sibling in match:
            insert_before(match, new_branch, sibling)


def config_mapping(cfg: dict, ctx: RepoContext, result: SyncResult) -> Mapping | None:
    pairs = [(b, r.rancher_minor) for _, b, r in preset_rules(cfg) if r.rancher_minor is not None]
    mapping, conflict = mapping_from_pairs(pairs, "existing branch presets")
    if conflict:
        result.warnings.append(f"New branches cannot be derived: {conflict}.")
        return mapping
    if mapping:
        return mapping
    supported = sorted(set(ctx.catalog.supported("main")) | set(ctx.catalog.supported("release")))
    return identity_mapping(ctx.branches, supported)


def roll_main(cfg: dict, ref: str, ctx: RepoContext, mapping: Mapping | None, result: SyncResult) -> None:
    """Keep `rancher-main` on the newest version branch of version-branch repos."""
    while True:
        main_rules = [(rule, b, r) for rule, b, r in preset_rules(cfg) if r.name == "rancher-main"]
        if len(main_rules) != 1 or not (parsed := Branch.parse(main_rules[0][1])):
            return
        rule, branch, main_ref = main_rules[0]
        newer = parsed.with_minor(parsed.minor + 1)
        if newer not in ctx.branches:
            return
        rancher = mapping.rancher_for(branch) if mapping else None
        if rancher is None:
            result.warnings.append(f"`{newer}` exists, but the Rancher version of `{branch}` is unknown; `rancher-main` not moved.")
            return
        name = f"rancher-2.{rancher}"
        if ctx.catalog.status(ref, name) != "ok":
            result.warnings.append(f"`{newer}` exists, but `{name}` is not available on `#{ref}`; `rancher-main` stays on `{branch}`.")
            return
        new_rule = copy.deepcopy(rule)
        new_rule["matchBaseBranches"] = [newer]
        if isinstance(rule.get("description"), str):
            new_rule["description"] = rule["description"].replace(branch, newer)
        rule["extends"] = [
            preset_ref(name, ref, main_ref.sep) if (p := PresetRef.parse(e)) and p.name == "rancher-main" else e
            for e in rule["extends"]
        ]
        cfg["packageRules"].insert(cfg["packageRules"].index(rule), new_rule)
        add_branch_to_lists(cfg, newer, branch)
        result.changes.append(f"Move `rancher-main` from `{branch}` to `{newer}` and apply `{name}` to `{branch}`.")


def add_new_presets(cfg: dict, ref: str, ctx: RepoContext, mapping: Mapping | None, result: SyncResult) -> None:
    """Add supported presets newer than the newest one in use, modelled on its rule.

    The branch comes from the branch-to-preset mapping and is only used when it
    exists in the repository; a preset that is not promoted to the used ref yet
    is reported instead of added.
    """
    versioned = [item for item in preset_rules(cfg) if item[2].rancher_minor is not None]
    if not versioned:
        return
    mapped = {b for _, b, _ in preset_rules(cfg)}
    sibling, sibling_branch, sibling_ref = max(versioned, key=lambda item: item[2].rancher_minor)
    skipped = []
    for minor in ctx.catalog.supported(ref):
        if minor <= sibling_ref.rancher_minor:
            continue
        name = f"rancher-2.{minor}"
        branch = mapping.branch_for(minor) if mapping else None
        if branch in mapped:
            continue
        if not branch or branch not in ctx.branches:
            skipped.append(f"`{name}` (branch `{branch}` does not exist)" if branch else f"`{name}` (branch unknown)")
            continue
        new_rule = {}
        if isinstance(sibling.get("description"), str):
            new_rule["description"] = (
                sibling["description"].replace(sibling_branch, branch).replace(f"2.{sibling_ref.rancher_minor}", f"2.{minor}")
            )
        new_rule["matchBaseBranches"] = [branch]
        new_rule["extends"] = [preset_ref(name, ref, sibling_ref.sep)]
        cfg["packageRules"].insert(cfg["packageRules"].index(sibling), new_rule)
        add_branch_to_lists(cfg, branch, sibling_branch)
        mapped.add(branch)
        result.changes.append(f"Add preset `{name}` for branch `{branch}`.")
        sibling, sibling_branch, sibling_ref = new_rule, branch, PresetRef.parse(new_rule["extends"][0])
    if skipped:
        result.notes.append(f"Not added until a matching branch exists: {', '.join(skipped)}.")
    pending = [
        f"`rancher-2.{m}` (`{branch}`)" for m in ctx.catalog.supported("main")
        if m > sibling_ref.rancher_minor and ctx.catalog.status(ref, f"rancher-2.{m}") == "unreleased"
        and mapping and (branch := mapping.branch_for(m)) in ctx.branches and branch not in mapped
    ]
    if pending:
        result.notes.append(f"Not added until promoted to `#{ref}`: {', '.join(pending)}.")


def template_description(template: dict) -> str | None:
    for rule, branch, ref in preset_rules(template):
        if ref.rancher_minor is not None and isinstance(rule.get("description"), str):
            return rule["description"].replace(branch, "{branch}").replace(f"2.{ref.rancher_minor}", "{version}")
    return None


def populate(cfg: dict, ref: str, ctx: RepoContext, result: SyncResult) -> dict:
    """Add `files/renovate.json` style Rancher presets to configs without any.

    Skipped, with the reason reported, when the default branch is a version
    branch, no Rancher version maps to an existing branch, or the config has
    custom package rules.
    """
    if Branch.parse(ctx.default_branch):
        result.warnings.append(f"Not populated: default branch `{ctx.default_branch}` is a version branch; add presets manually.")
        return cfg
    supported = ctx.catalog.supported(ref)
    entries = ctx.oracle.get(normalize_name(ctx.repo.split("/")[-1]), [])
    mapping, conflict = oracle_mapping(entries, ctx.branches)
    if conflict:
        result.warnings.append(f"Pinned rancher/rancher versions: {conflict}; using exact matches only.")
    mapping = mapping or identity_mapping(ctx.branches, supported)
    if not mapping:
        if not any(Branch.parse(b) for b in ctx.branches):
            reason = "no version branches"
        elif entries:
            reason = "no branch matches a component version pinned in rancher/rancher"
        else:
            reason = "Rancher version of the branches is unknown"
        result.warnings.append(f"Not populated: {reason}.")
        return cfg
    targets = [
        (m, b) for m in sorted(supported, reverse=True)
        if (b := mapping.branch_for(m)) and b in ctx.branches and b != ctx.default_branch
    ]
    if not targets:
        result.warnings.append("Not populated: no branch exists for a supported Rancher version.")
        return cfg
    mapped = ", ".join(f"`{b}` -> `rancher-2.{m}`" for m, b in targets)
    if any(not is_preset_only_rule(r) for r in rules_of(cfg)):
        result.warnings.append(
            f"Not populated: config has custom package rules; suggested presets: `{ctx.default_branch}` -> `rancher-main`, "
            f"{mapped} (derived from {mapping.source})."
        )
        return cfg
    patterns = list(cfg.get("baseBranchPatterns") or [ctx.default_branch])
    if ctx.default_branch not in patterns:
        patterns.insert(0, ctx.default_branch)
    rules = [{"matchBaseBranches": [ctx.default_branch], "extends": [preset_ref("rancher-main", ref)]}]
    for minor, branch in targets:
        if branch not in patterns:
            patterns.append(branch)
        rule = {}
        if ctx.template_description:
            rule["description"] = ctx.template_description.format(version=f"2.{minor}", branch=branch)
        rule["matchBaseBranches"] = [branch]
        rule["extends"] = [preset_ref(f"rancher-2.{minor}", ref)]
        rules.append(rule)
    cfg = with_key_after(cfg, "baseBranchPatterns", patterns, "extends")
    cfg["packageRules"] = rules + list(cfg.get("packageRules") or [])
    result.changes.append(f"Add Rancher presets: `{ctx.default_branch}` -> `rancher-main`, {mapped} (derived from {mapping.source}).")
    return cfg


def sync_config(original: dict, ctx: RepoContext) -> SyncResult:
    """Apply the consumer config rules, in this order.

    Configs without any `rancher/renovate-config` preset are left untouched.
    Configs with a `rancher-2.x` preset keep a single preset ref, get the root
    `default` preset, lose EOL presets and their branches, keep `rancher-main`
    on the newest version branch, and gain supported presets whose branch is
    derived from the existing branch-to-preset offset. Branches are only added
    when they exist in the repository and the preset exists on the used ref.
    Configs without a Rancher preset are populated from the example template
    when `populate` is set and the branch-to-version mapping is known.
    """
    cfg = copy.deepcopy(original)
    result = SyncResult(cfg)
    ref = normalize_refs(cfg, result)
    if not all_refs(cfg):
        result.notes.append(f"Does not use {PRESET_REPO} presets; left unchanged.")
        return result
    cfg = rename_base_branches(cfg, result)
    resolve_default_branch(cfg, ctx.default_branch, result)
    cfg = ensure_root_default(cfg, ref, result)
    if rancher_refs(all_refs(cfg)):
        remove_eol(cfg, ref, ctx, result)
        mapping = config_mapping(cfg, ctx, result)
        roll_main(cfg, ref, ctx, mapping, result)
        add_new_presets(cfg, ref, ctx, mapping, result)
    elif ctx.populate and ctx.repo.startswith("rancher/"):
        cfg = populate(cfg, ref, ctx, result)
    if cfg.get("packageRules") == [] and "packageRules" not in original:
        del cfg["packageRules"]
    for items in extends_lists(cfg):
        items[:] = list(dict.fromkeys(items))
    missing = {r.name for r in all_refs(cfg) if ctx.catalog.status(ref, r.name) == "missing"}
    result.warnings += [f"Preset `{name}` does not exist on `#{ref}`." for name in sorted(missing - {"default"})]
    result.config = cfg
    return result


# --- Report helpers ----------------------------------------------------------


def count_leaves(value: object) -> int:
    if isinstance(value, dict):
        return sum(count_leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(count_leaves(v) for v in value)
    return 1


def custom_percentage(cfg: dict) -> int:
    """Share of JSON leaves outside the parts managed by this sync."""
    total = count_leaves(cfg)
    managed = count_leaves([cfg.get(k) for k in ("$schema", "baseBranchPatterns") if k in cfg])
    managed += len(parse_refs(cfg.get("extends")))
    managed += sum(count_leaves(r) for r in rules_of(cfg) if r.get("extends") and is_preset_only_rule(r))
    return round(100 * (total - managed) / total) if total else 0


def base_branches(cfg: dict, default_branch: str) -> list[str]:
    patterns = cfg.get("baseBranchPatterns") or cfg.get("baseBranches") or [default_branch]
    return [default_branch if b == "$default" else b for b in patterns]


def rule_applies(rule: dict, branch: str) -> bool:
    match = rule.get("matchBaseBranches")
    return not match or any(branch_matches(p, branch) for p in match)


def majors_allowed(cfg: dict, default_branch: str) -> list[str]:
    """Base branches without a Rancher release preset or major-disabling rule."""
    if cfg.get("enabled") is False:
        return []
    rules = rules_of(cfg)
    root_restricted = any(r.rancher_minor is not None for r in parse_refs(cfg.get("extends")))
    allowed = []
    for branch in base_branches(cfg, default_branch):
        applying = [r for r in rules if rule_applies(r, branch)]
        restricted = root_restricted or any(p.rancher_minor is not None for r in applying for p in parse_refs(r.get("extends")))
        disabled = any(
            r.get("enabled") is False
            and "major" in (r.get("matchUpdateTypes") or [])
            and set(r) <= {"description", "matchBaseBranches", "matchUpdateTypes", "enabled"}
            for r in applying
        )
        if not restricted and not disabled:
            allowed.append(branch)
    return allowed


def has_automerge(value: object) -> bool:
    if isinstance(value, dict):
        return value.get("automerge") is True or any(has_automerge(v) for v in value.values())
    if isinstance(value, list):
        return any(has_automerge(v) for v in value)
    return False


def automerge_mode(cfg: dict) -> str:
    preset = any(r.name == "automerge" for r in all_refs(cfg))
    custom = has_automerge(cfg)
    if preset:
        return "preset + custom" if custom else "preset (`automerge.json`)"
    return "custom" if custom else "no"


def canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def is_scalar_list(value: object) -> bool:
    return isinstance(value, list) and not any(isinstance(v, (dict, list)) for v in value)


TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]:,]|\n|[^\s{}\[\]:,"]+|[ \t\r]+')


@dataclass
class JsonStyle:
    """Formatting of an existing file: indent and which scalar lists were written on one line."""

    indent: str = "  "
    lists: dict[tuple[str | None, str], list[bool]] = field(default_factory=dict)
    keys: dict[str | None, list[bool]] = field(default_factory=dict)
    newline: bool = True

    @classmethod
    def detect(cls, text: str) -> "JsonStyle":
        style = cls(newline=text.endswith("\n") or not text)
        if match := re.search(r"^([ \t]+)\S", text, re.M):
            style.indent = match.group(1)
        stack: list[dict] = []
        for match in TOKEN_RE.finditer(text):
            token, top = match.group(), (stack[-1] if stack else None)
            if token in ("{", "["):
                key = top["key"] if top and not top["list"] else None
                stack.append({"list": token == "[", "parent_key": key, "start": match.start(), "inline": True, "key": None, "want_key": True})
            elif token in ("}", "]") and top:
                stack.pop()
                value = json.loads(text[top["start"] : match.end()])
                style.lists.setdefault((top["parent_key"], canonical_json(value)), []).append(top["inline"])
                if top["list"] and is_scalar_list(value):
                    style.keys.setdefault(top["parent_key"], []).append(top["inline"])
            elif token == "\n":
                for frame in stack:
                    frame["inline"] = False
            elif token == ":" and top:
                top["want_key"] = False
            elif token == "," and top:
                top["want_key"] = True
            elif token.strip() and top and not top["list"] and top["want_key"]:
                top["key"] = json.loads(token)
        return style

    def inline(self, key: str | None, value: object) -> bool:
        canonical = canonical_json(value)
        # Identical lists may differ in style; consume them in file order.
        if seen := self.lists.get((key, canonical)):
            return seen.pop(0) if len(seen) > 1 else seen[0]
        if not is_scalar_list(value):
            return False
        votes = self.keys.get(key) or [v for vs in self.keys.values() for v in vs]
        return bool(votes) and sum(votes) * 2 > len(votes)


def render_json(cfg: dict, style: JsonStyle | None = None) -> str:
    style = copy.deepcopy(style) if style else JsonStyle()

    def render(value: object, key: str | None, depth: int) -> str:
        pad, inner = style.indent * depth, style.indent * (depth + 1)
        if not isinstance(value, (dict, list)) or not value or style.inline(key, value):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, dict):
            items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {render(v, k, depth + 1)}" for k, v in value.items()]
            return "{\n" + ",\n".join(items) + f"\n{pad}}}"
        return "[\n" + ",\n".join(f"{inner}{render(v, None, depth + 1)}" for v in value) + f"\n{pad}]"

    return render(cfg, None, 0) + ("\n" if style.newline else "")


def pr_body(result: SyncResult, run_url: str | None, actor: str | None) -> str:
    lines = [
        f"Update the Renovate configuration to the current presets and workflow of [{PRESET_REPO}](https://github.com/{PRESET_REPO}).",
        "",
        "Changes:",
        *[f"- {c}" for c in result.changes],
    ]
    if result.warnings:
        lines += ["", "Not changed automatically, please review:", *[f"- {w}" for w in result.warnings]]
    if result.notes:
        lines += ["", "Notes:", *[f"- {n}" for n in result.notes]]
    origin = f"[renovate-sync workflow]({run_url})" if run_url else "renovate-sync workflow"
    lines += ["", f"Created by the {origin}" + (f", started by @{actor}." if actor else ".")]
    return "\n".join(lines) + "\n"


# --- Repository processing ---------------------------------------------------


@dataclass
class RepoReport:
    repo: str
    status: str
    active: str = ""
    ref: str = ""
    pr: str = ""
    majors: str = ""
    automerge: str = ""
    custom: str = ""
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dependabot: bool = False
    diff: str = ""


def renovate_workflow_state(gh: GitHub, repo: str, workflows: set[str]) -> str | None:
    data = gh.request("GET", f"repos/{repo}/actions/workflows?per_page=100", allow_missing=True)
    if data is None:
        # Without Actions read access only the workflow file can be detected.
        return "unknown" if any("renovate" in name.lower() for name in workflows) else None
    states = [w["state"] for w in data.get("workflows", []) if "renovate" in w["path"].lower()]
    if not states:
        return None
    return "active" if "active" in states else states[0]


def activity(original: dict, workflow: str | None) -> str:
    if original.get("enabled") is False:
        return "no (`enabled: false`)"
    if workflow is None:
        return "no (no Renovate workflow)"
    if workflow == "unknown":
        return "yes (workflow state unknown)"
    return "yes" if workflow == "active" else f"no (workflow {workflow})"


def sync_prs(gh: GitHub, repo: str, state: str) -> list:
    """Pull requests from the sync branch, newest first."""
    head = urllib.parse.quote(f"{repo.split('/')[0]}:{SYNC_BRANCH}", safe="")
    return gh.request("GET", f"repos/{repo}/pulls?state={state}&head={head}") or []


def drop_merged_sync_branch(gh: GitHub, repo: str) -> str | None:
    """Delete the sync branch when its latest pull request was merged."""
    if not gh.request("GET", f"repos/{repo}/git/ref/heads/{SYNC_BRANCH}", allow_missing=True):
        return None
    prs = sync_prs(gh, repo, "all")
    if not prs or not prs[0].get("merged_at"):
        return None
    try:
        gh.request("DELETE", f"repos/{repo}/git/refs/heads/{SYNC_BRANCH}")
    except RuntimeError as err:
        # A deletion whose response was lost reports 404 on the retry, so a
        # missing ref means the cleanup happened. Permission and ruleset
        # failures must still fail the repository.
        if "HTTP 404" not in str(err):
            raise
    return f"Delete branch `{SYNC_BRANCH}` left behind by merged pull request #{prs[0]['number']}."


def strip_credit(body: str) -> str:
    """PR body without its terminal credit line, which names the run and actor."""
    lines = body.rstrip().split("\n")
    return "\n".join(lines[:-1]).rstrip() if lines and lines[-1].startswith("Created by the ") else body.rstrip()


def sync_branch_current(gh: GitHub, repo: str, default_branch: str, updates: dict[str, str | None]) -> bool:
    """True when the sync branch differs from the default branch exactly by the proposal."""
    base = urllib.parse.quote(default_branch)
    files = gh.request("GET", f"repos/{repo}/compare/{base}...{SYNC_BRANCH}").get("files") or []
    return {f["filename"] for f in files} == set(updates) and all(
        gh.file(repo, path, SYNC_BRANCH) == content for path, content in updates.items()
    )


def open_sync_pr(gh: GitHub, repo: str, default_branch: str, updates: dict[str, str | None], body: str) -> str:
    """Commit the files to the sync branch through the API and open or update its PR.

    A sync branch and PR that already carry the proposal are left untouched, so
    repeated runs neither force-push nor rewrite the PR.
    """
    sync_ref = gh.request("GET", f"repos/{repo}/git/ref/heads/{SYNC_BRANCH}", allow_missing=True)
    if not sync_ref or not sync_branch_current(gh, repo, default_branch, updates):
        base_sha = gh.request("GET", f"repos/{repo}/git/ref/heads/{urllib.parse.quote(default_branch)}")["object"]["sha"]
        base_tree = gh.request("GET", f"repos/{repo}/git/commits/{base_sha}")["tree"]["sha"]
        items = [
            {"path": path, "mode": "100644", "type": "blob", **({"sha": None} if content is None else {"content": content})}
            for path, content in updates.items()
        ]
        tree = gh.request("POST", f"repos/{repo}/git/trees", {"base_tree": base_tree, "tree": items})["sha"]
        commit = gh.request("POST", f"repos/{repo}/git/commits", {"message": PR_TITLE, "tree": tree, "parents": [base_sha]})["sha"]
        if sync_ref:
            gh.request("PATCH", f"repos/{repo}/git/refs/heads/{SYNC_BRANCH}", {"sha": commit, "force": True})
        else:
            gh.request("POST", f"repos/{repo}/git/refs", {"ref": f"refs/heads/{SYNC_BRANCH}", "sha": commit})
    existing = sync_prs(gh, repo, "open")
    if not existing:
        return gh.request("POST", f"repos/{repo}/pulls", {"title": PR_TITLE, "body": body, "head": SYNC_BRANCH, "base": default_branch})["html_url"]
    pr = existing[0]
    if pr.get("title") != PR_TITLE or strip_credit(pr.get("body") or "") != strip_credit(body):
        pr = gh.request("PATCH", f"repos/{repo}/pulls/{pr['number']}", {"title": PR_TITLE, "body": body})
    return pr["html_url"]


def write_proposal(out: str, repo: str, updates: dict[str, str | None], diff: str) -> None:
    target = Path(out) / repo.replace("/", "__")
    target.mkdir(parents=True, exist_ok=True)
    # Flat names: upload-artifact skips hidden directories such as `.github`.
    for path, content in updates.items():
        if content is not None:
            (target / Path(path).name).write_text(content)
    (target / "changes.diff").write_text(diff)


def unified_diff(path: str, old: str | None, new: str | None) -> str:
    lines = difflib.unified_diff(
        (old or "").splitlines(True),
        (new or "").splitlines(True),
        f"a/{path}" if old is not None else "/dev/null",
        f"b/{path}" if new is not None else "/dev/null",
    )
    return "".join(lines)


@dataclass
class Shared:
    """Inputs computed once per run."""

    catalog: PresetCatalog
    oracle: dict = field(default_factory=dict)
    description: str | None = None
    # Rendered renovate-vault.yml; None when workflows are not updated.
    workflow: str | None = None
    release: str = ""
    # files/auto-approve-bot-prs.yml; None when automerge is not enabled.
    approve: str | None = None


def workflow_updates(gh: GitHub, repo: str, branch: str, workflows: set[str], shared: Shared, result: SyncResult) -> dict[str, str | None]:
    """Replace the Renovate workflow with the current template, dropping obsolete ones.

    Only workflows that already exist are touched: repositories without a
    Renovate workflow do not get one.
    """
    if shared.workflow is None:
        return {}
    obsolete = [p for p in OBSOLETE_WORKFLOWS if Path(p).name in workflows]
    has_current = Path(WORKFLOW_PATH).name in workflows
    if not has_current and not obsolete:
        result.notes.append("No Renovate workflow found; none added.")
        return {}
    updates: dict[str, str | None] = dict.fromkeys(obsolete)
    if not has_current or gh.file(repo, WORKFLOW_PATH, branch) != shared.workflow:
        updates[WORKFLOW_PATH] = shared.workflow
    for path in obsolete:
        result.changes.append(f"Replace obsolete `{path}` with `{WORKFLOW_PATH}` pinned to `{shared.release}`.")
    if has_current and WORKFLOW_PATH in updates:
        result.changes.append(f"Update `{WORKFLOW_PATH}` to the current template pinned to `{shared.release}`.")
    return updates


# --- Automerge ---------------------------------------------------------------


def required_checks(gh: GitHub, repo: str, branch: str) -> bool | None:
    """Whether branch protection or a ruleset requires a status check; None if the branch is missing."""
    quoted = urllib.parse.quote(branch, safe="")
    data = gh.request("GET", f"repos/{repo}/branches/{quoted}", allow_missing=True)
    if data is None:
        return None
    checks = (data.get("protection") or {}).get("required_status_checks") or {}
    if checks.get("enforcement_level") != "off" and (checks.get("contexts") or checks.get("checks")):
        return True
    rules = gh.request("GET", f"repos/{repo}/rules/branches/{quoted}", allow_missing=True) or []
    return any(r.get("type") == "required_status_checks" and (r.get("parameters") or {}).get("required_status_checks") for r in rules)


def automerge_blockers(gh: GitHub, info: dict, branches: list[str], active: str) -> list[str]:
    """Reasons automerge cannot be enabled safely; empty when it can.

    Renovate must be active and its activity confirmed, the repository must
    allow auto-merge, and every exact base branch must require a status check
    through branch protection or a ruleset.
    """
    if not active.startswith("yes"):
        return ["Renovate is not active"]
    if active != "yes":
        return [f"Renovate activity is unconfirmed (`{active}`)"]
    repo = info["full_name"]
    # GraphQL exposes autoMergeAllowed with read access; REST hides allow_auto_merge without push access.
    owner, name = repo.split("/", 1)
    allowed = (gh.graphql(AUTO_MERGE_QUERY, owner=owner, name=name).get("repository") or {}).get("autoMergeAllowed")
    if allowed is None:
        return ["the auto-merge repository setting is not readable"]
    if not allowed:
        return ["auto-merge is disabled in the repository settings"]
    blockers = []
    for branch in branches:
        if not is_exact_branch(branch):
            blockers.append(f"branch pattern `{branch}` cannot be checked")
        elif (found := required_checks(gh, repo, branch)) is None:
            blockers.append(f"branch `{branch}` does not exist")
        elif not found:
            blockers.append(f"`{branch}` does not require a status check")
    return blockers


def approve_variant(content: str | None) -> str:
    if content is None:
        return "unknown"
    if "github/token/" in content:
        return "Vault token"
    if "create-github-app-token" in content:
        return "App credentials"
    return "custom"


def enable_automerge(cfg: dict, branches: list[str], result: SyncResult) -> None:
    """Apply the shared automerge preset to all base branches, as in files/renovate.json consumers."""
    refs = all_refs(cfg)
    ref = refs[0].ref if refs and refs[0].ref else DEFAULT_REF
    sep = next((r.sep for r in refs if r.preset), "//")
    cfg.setdefault("packageRules", []).append({
        "description": "Activate automerge for eligible updates",
        "matchBaseBranches": list(branches),
        "extends": [preset_ref("automerge", ref, sep)],
    })
    result.changes.append(f"Enable automerge with the `automerge` preset for {', '.join(f'`{b}`' for b in branches)}.")


def approve_updates(repo: str, workflows: set[str], shared: Shared, result: SyncResult) -> dict[str, str | None]:
    """Add the auto-approve workflow unless an approve workflow already exists."""
    if any("approve" in name for name in workflows):
        return {}
    owner, name = repo.split("/", 1)
    result.changes.append(f"Add `{APPROVE_PATH}` to approve Renovate PRs that have auto-merge enabled.")
    result.notes.append(
        f"The auto-approve workflow needs the Vault role `github/token/{owner}--{name}--auto-approve--pull_requests--write`; "
        "request it before merging, otherwise approvals fail."
    )
    return {APPROVE_PATH: render_auto_approve(shared.approve or "", repo)}


def setup_automerge(gh: GitHub, info: dict, cfg: dict, workflows: set[str], active: str, shared: Shared, result: SyncResult) -> dict[str, str | None]:
    """Enable automerge and add the auto-approve workflow when that is safe.

    Skipped, with the reason reported, when the run did not ask for automerge,
    the config uses no Rancher preset or already has automerge, Renovate is not
    active, the repository does not allow auto-merge, or a base branch requires
    no status check. The approve workflow is only added when the repository has
    no approve workflow yet.
    """
    repo = info["full_name"]
    if shared.approve is None:
        return {}
    if not all_refs(cfg) or automerge_mode(cfg) != "no":
        return {}
    branches = base_branches(cfg, info["default_branch"])
    if blockers := automerge_blockers(gh, info, branches, active):
        result.notes.append(f"Automerge not enabled: {'; '.join(blockers)}.")
        return {}
    enable_automerge(cfg, branches, result)
    return approve_updates(repo, workflows, shared, result)


def automerge_label(gh: GitHub, info: dict, cfg: dict, workflows: set[str], approve_added: bool) -> str:
    label = automerge_mode(cfg)
    if approve_added:
        return f"{label}; approve: Vault token (added)"
    approvers = sorted(n for n in workflows if "approve" in n)
    if approvers and label != "no":
        content = gh.file(info["full_name"], f".github/workflows/{approvers[0]}", info["default_branch"])
        return f"{label}; approve: {approve_variant(content)}"
    return label


def dir_names(gh: GitHub, repo: str, path: str) -> set[str]:
    listing = gh.request("GET", f"repos/{repo}/contents/{path}", allow_missing=True) or []
    return {e["name"] for e in listing if isinstance(e, dict)}


def process_repo(gh: GitHub, info: dict, args: argparse.Namespace, shared: Shared) -> RepoReport:
    repo, default_branch = info["full_name"], info["default_branch"]
    report = RepoReport(repo, "no-config")
    names = dir_names(gh, repo, ".github")
    report.dependabot = bool(names & {"dependabot.yml", "dependabot.yaml"})
    if "renovate.json" not in names:
        report.active = "no Renovate config"
        return report
    raw = gh.file(repo, CONFIG_PATH, default_branch) or ""
    try:
        original = json.loads(raw)
    except ValueError as err:
        original = err
    if not isinstance(original, dict):
        report.status = "invalid"
        report.warnings.append(f"Invalid JSON: {original}")
        return report
    workflows = dir_names(gh, repo, ".github/workflows")
    ctx = RepoContext(repo, default_branch, gh.branches(repo), shared.catalog, shared.oracle, shared.description, args.populate)
    result = sync_config(original, ctx)
    cfg = result.config
    report.active = activity(original, renovate_workflow_state(gh, repo, workflows))
    approve_files = setup_automerge(gh, info, cfg, workflows, report.active, shared, result)
    report.ref = result.ref_label
    report.majors = ", ".join(f"`{b}`" for b in majors_allowed(cfg, default_branch)) or "none"
    report.automerge = automerge_label(gh, info, cfg, workflows, bool(approve_files))
    report.custom = f"{custom_percentage(cfg)}%"
    report.changes, report.warnings, report.notes = result.changes, result.warnings, result.notes
    updates: dict[str, str | None] = {}
    originals = {CONFIG_PATH: raw}
    if cfg != original:
        updates[CONFIG_PATH] = render_json(cfg, JsonStyle.detect(raw))
    for path, content in (workflow_updates(gh, repo, default_branch, workflows, shared, result) if all_refs(original) else {}).items():
        if Path(path).name in workflows:
            originals[path] = gh.file(repo, path, default_branch)
        updates[path] = content
    updates.update(approve_files)
    if not updates:
        report.status, report.pr = "unchanged", "no change"
        if not args.dry_run and (dropped := drop_merged_sync_branch(gh, repo)):
            report.notes.append(dropped)
        return report
    report.status = "changed"
    report.diff = "".join(unified_diff(path, originals.get(path), content) for path, content in updates.items())
    if args.out:
        write_proposal(args.out, repo, updates, report.diff)
    if args.dry_run:
        report.pr = "dry run: would update"
    else:
        report.pr = open_sync_pr(gh, repo, default_branch, updates, pr_body(result, args.run_url, args.actor))
    return report


def list_targets(gh: GitHub, target: str) -> list[dict]:
    target = target.strip().strip("/")
    if "/" in target:
        repos = [gh.request("GET", f"repos/{target}")]
    else:
        kind = gh.request("GET", f"users/{target}")["type"]
        path = f"orgs/{target}/repos?type=all" if kind == "Organization" else f"users/{target}/repos?type=owner"
        repos = skip_security_mirrors([r for r in gh.paginate(path) if not r.get("archived") and not r.get("disabled")])
    # This repository holds the presets and the workflow templates deployed to
    # consumers, so it is the source of the sync rather than a target.
    return sorted((r for r in repos if r["full_name"].lower() != PRESET_REPO), key=lambda r: r["full_name"].lower())


def skip_security_mirrors(repos: list[dict]) -> list[dict]:
    """Drop `<name>-security` repos whose `<name>` repo is also listed."""
    names = {r["name"] for r in repos}
    return [r for r in repos if not (r["name"].endswith("-security") and r["name"].removesuffix("-security") in names)]


def cell(value: str) -> str:
    return value.replace("|", "\\|")


def pr_cell(value: str) -> str:
    return f"[#{value.rsplit('/', 1)[-1]}]({value})" if value.startswith("https://") else value


def render_summary(target: str, dry_run: bool, reports: list[RepoReport], diffs: bool = True) -> str:
    configured = [r for r in reports if r.status in ("changed", "unchanged")]
    by_status = {s: [r for r in reports if r.status == s] for s in ("no-config", "invalid", "error")}
    neither = [r for r in by_status["no-config"] if not r.dependabot]
    lines = [
        f"## Renovate config sync for `{target}`{' (dry run)' if dry_run else ''}",
        "",
        (
            f"{len(configured)} configured, {sum(r.status == 'changed' for r in configured)} with changes, "
            f"{len(by_status['no-config'])} without `{CONFIG_PATH}` ({len(by_status['no-config']) - len(neither)} with Dependabot), "
            f"{len(by_status['invalid'])} invalid, {len(by_status['error'])} errors."
        ),
        "",
        "| Repository | Renovate active | Preset ref | Pull request | Majors allowed on | Automerge | Custom % |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in configured:
        lines.append(
            f"| [{r.repo}](https://github.com/{r.repo}) | {cell(r.active)} | {cell(r.ref)} | {cell(pr_cell(r.pr))} "
            f"| {cell(r.majors)} | {cell(r.automerge)} | {r.custom} |"
        )
    detailed = [r for r in configured if r.changes or r.warnings or r.notes]
    if detailed:
        lines += ["", "### Changes and notes", ""]
        for r in detailed:
            lines += [f"<details><summary>{r.repo}</summary>", ""]
            lines += [f"- {c}" for c in r.changes] + [f"- :warning: {w}" for w in r.warnings] + [f"- {n}" for n in r.notes]
            if diffs and r.diff:
                lines += ["", "````diff", r.diff.rstrip("\n"), "````"]
            lines += ["", "</details>"]
    sections = [
        ("Invalid configs", [f"`{r.repo}`: {'; '.join(r.warnings)}" for r in by_status["invalid"]]),
        ("Errors", [f"`{r.repo}`: {'; '.join(r.warnings)}" for r in by_status["error"]]),
        ("Renovate inactive with Dependabot config", [f"`{r.repo}`: {r.active}" for r in reports if r.dependabot and not r.active.startswith("yes")]),
    ]
    for title, items in sections:
        if items:
            lines += ["", f"### {title}", "", *[f"- {i}" for i in items]]
    if neither:
        lines += ["", f"<details><summary>Repositories with neither Renovate nor Dependabot ({len(neither)})</summary>", ""]
        lines += [f"- `{r.repo}`" for r in neither] + ["", "</details>"]
    if not diffs:
        lines += ["", "Diffs are omitted to stay within the job summary size limit; see the `renovate-config-sync` artifact."]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True, help="organization (rancher) or repository (rancher/fleet)")
    parser.add_argument("--apply", dest="dry_run", action="store_false", help="create or update pull requests")
    parser.add_argument("--no-populate", dest="populate", action="store_false", help="skip configs without Rancher presets")
    parser.add_argument("--template", default="files/renovate.json", help="example consumer config used to populate configs")
    parser.add_argument("--workflow-template", default="files/renovate-vault.yml", help="Renovate workflow deployed to consumers")
    parser.add_argument("--no-update-workflow", dest="update_workflow", action="store_false", help="keep consumer Renovate workflows")
    parser.add_argument("--automerge", action="store_true", help="enable automerge and auto-approve where the repository allows it")
    parser.add_argument("--approve-template", default="files/auto-approve-bot-prs.yml", help="auto-approve workflow deployed with --automerge")
    parser.add_argument("--summary", help="append the Markdown report to this file instead of stdout")
    parser.add_argument("--out", help="directory for proposed configs and diffs")
    parser.add_argument("--run-url")
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    gh = GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    catalog = load_catalog(gh)
    minors = set(catalog.supported("main")) | set(catalog.supported("release"))
    shared = Shared(
        catalog,
        load_oracle(gh, minors) if args.populate else {},
        template_description(json.loads(Path(args.template).read_text())),
    )
    if args.update_workflow:
        sha, shared.release = latest_release(lambda path: gh.request("GET", path, allow_missing=True))
        shared.workflow = render_renovate_workflow(Path(args.workflow_template).read_text(), sha, shared.release)
    if args.automerge:
        shared.approve = Path(args.approve_template).read_text()
    reports = []
    for info in list_targets(gh, args.target):
        try:
            report = process_repo(gh, info, args, shared)
        except RuntimeError as err:
            report = RepoReport(info["full_name"], "error", warnings=[str(err)])
        print(f"{report.repo}: {report.status} {report.pr}".rstrip(), file=sys.stderr)
        reports.append(report)
    summary = render_summary(args.target, args.dry_run, reports)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "summary.md").write_text(summary)
    if len(summary.encode()) > SUMMARY_LIMIT:
        summary = render_summary(args.target, args.dry_run, reports, diffs=False)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    else:
        sys.stdout.write(summary)
    return 1 if any(r.status == "error" for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
