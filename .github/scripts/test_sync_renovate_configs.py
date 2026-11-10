import argparse
import json
import unittest

import sync_renovate_configs as s

EOL = {"packageRules": [{"matchPackageNames": ["*"], "enabled": False}]}
OK = {"packageRules": [{"matchPackageNames": ["go"], "allowedVersions": "<1.27"}]}


def catalog(main=range(12, 17), release=range(12, 16), eol=(9, 10, 11)):
    def files(minors, eol_minors):
        out = {"default": {}, "rancher-main": {}, "automerge": {}}
        out.update({f"rancher-2.{m}": EOL for m in eol_minors})
        out.update({f"rancher-2.{m}": OK for m in minors})
        return out

    return s.PresetCatalog({"main": files(main, eol), "release": files(release, eol)})


def ref(name, ref="release", sep="//"):
    return f"github>rancher/renovate-config{sep}{name}#{ref}"


def rule(branch, name, ref_name="release", sep="//", **extra):
    return {**extra, "matchBaseBranches": [branch], "extends": [ref(name, ref_name, sep)]}


def ctx(repo="rancher/foo", default="main", branches=(), cat=None, **kwargs):
    return s.RepoContext(repo, default, {default, *branches}, cat or catalog(), **kwargs)


def branch_rules(cfg):
    return {branch: f"{p.name}#{p.ref}" for _, branch, p in s.preset_rules(cfg)}


class FleetStyleTest(unittest.TestCase):
    """Main-ref consumer with an offset mapping and an automerge rule."""

    def config(self):
        return {
            "extends": [ref("default", "main"), ref("automerge", "main")],
            "baseBranchPatterns": ["main", "release/v0.15", "release/v0.14", "release/v0.13", "release/v0.12"],
            "packageRules": [
                rule("main", "rancher-main", "main"),
                rule("release/v0.15", "rancher-2.14", "main", description="Rancher 2.14 for release/v0.15"),
                rule("release/v0.14", "rancher-2.13", "main"),
                rule("release/v0.13", "rancher-2.12", "main"),
                rule("release/v0.12", "rancher-2.11", "main"),
                {
                    "matchBaseBranches": ["release/v0.15", "release/v0.14", "release/v0.13", "release/v0.12"],
                    "matchUpdateTypes": ["patch"],
                    "automerge": True,
                },
            ],
        }

    def test_removes_eol_and_adds_new_branch(self):
        result = s.sync_config(self.config(), ctx(branches={"release/v0.16", "release/v0.15", "release/v0.12"}))
        cfg = result.config
        self.assertEqual(result.ref_label, "main")
        self.assertEqual(
            cfg["baseBranchPatterns"],
            ["main", "release/v0.16", "release/v0.15", "release/v0.14", "release/v0.13"],
        )
        self.assertEqual(branch_rules(cfg)["release/v0.16"], "rancher-2.15#main")
        self.assertNotIn("release/v0.12", branch_rules(cfg))
        new_rule = next(r for r in cfg["packageRules"] if r["matchBaseBranches"] == ["release/v0.16"])
        self.assertEqual(new_rule["description"], "Rancher 2.15 for release/v0.16")
        automerge = cfg["packageRules"][-1]["matchBaseBranches"]
        self.assertEqual(automerge, ["release/v0.16", "release/v0.15", "release/v0.14", "release/v0.13"])
        self.assertIn("Not added until a matching branch exists: `rancher-2.16` (branch `release/v0.17` does not exist).", result.notes)

    def test_unchanged_when_current(self):
        cfg = self.config()
        cfg["baseBranchPatterns"].remove("release/v0.12")
        cfg["packageRules"] = [r for r in cfg["packageRules"] if r["matchBaseBranches"] != ["release/v0.12"]]
        cfg["packageRules"][-1]["matchBaseBranches"].remove("release/v0.12")
        result = s.sync_config(cfg, ctx(cat=catalog(main=range(12, 15))))
        self.assertEqual(result.config, cfg)
        self.assertEqual(result.changes, [])


class TransformTest(unittest.TestCase):
    def test_colon_style_kept(self):
        cfg = {
            "extends": [ref("default")],
            "baseBranchPatterns": ["main", "release/v0.11"],
            "packageRules": [rule("main", "rancher-main", sep=":"), rule("release/v0.11", "rancher-2.15", sep=":")],
        }
        cat = catalog(main=range(12, 17), release=range(12, 17))
        result = s.sync_config(cfg, ctx(branches={"release/v0.12"}, cat=cat))
        self.assertIn(ref("rancher-2.16", sep=":"), result.config["packageRules"][1]["extends"])

    def test_offset_zero_dev_branches(self):
        cfg = {
            "extends": [ref("default")],
            "baseBranchPatterns": ["dev-v2.15", "dev-v2.14", "dev-v2.11"],
            "packageRules": [rule(f"dev-v2.{m}", f"rancher-2.{m}") for m in (15, 14, 11)],
        }
        cat = catalog(main=range(12, 17), release=range(12, 17))
        result = s.sync_config(cfg, ctx(default="dev-v2.15", branches={"dev-v2.16", "dev-v2.14", "dev-v2.11"}, cat=cat))
        self.assertEqual(result.config["baseBranchPatterns"], ["dev-v2.16", "dev-v2.15", "dev-v2.14"])
        self.assertEqual(branch_rules(result.config)["dev-v2.16"], "rancher-2.16#release")

    def test_unreleased_preset_noted(self):
        cfg = {
            "extends": [ref("default")],
            "baseBranchPatterns": ["dev-v2.15"],
            "packageRules": [rule("dev-v2.15", "rancher-2.15")],
        }
        result = s.sync_config(cfg, ctx(default="dev-v2.15", branches={"dev-v2.16"}))
        self.assertEqual(result.changes, [])
        self.assertIn("Not added until promoted to `#release`: `rancher-2.16` (`dev-v2.16`).", result.notes)

    def test_rolls_rancher_main_to_newest_version_branch(self):
        cfg = {
            "extends": [ref("default")],
            "baseBranchPatterns": ["release/v1.2", "release/v1.1"],
            "packageRules": [rule("release/v1.2", "rancher-main"), rule("release/v1.1", "rancher-2.14")],
        }
        result = s.sync_config(cfg, ctx(default="release/v1.2", branches={"release/v1.1", "release/v1.3"}))
        rules = branch_rules(result.config)
        self.assertEqual(rules["release/v1.3"], "rancher-main#release")
        self.assertEqual(rules["release/v1.2"], "rancher-2.15#release")
        self.assertEqual(result.config["baseBranchPatterns"], ["release/v1.3", "release/v1.2", "release/v1.1"])

    def test_mixed_refs_become_release(self):
        cfg = {"extends": [ref("default", "main")], "packageRules": [rule("main", "rancher-main"), rule("v1", "rancher-2.15", "")]}
        cfg["packageRules"][1]["extends"] = ["github>rancher/renovate-config//rancher-2.15"]
        result = s.sync_config(cfg, ctx())
        self.assertEqual(result.ref_label, "mixed -> release")
        self.assertEqual({r.ref for r in s.all_refs(result.config)}, {"release"})

    def test_root_default_and_base_branches(self):
        cfg = {
            "$schema": "https://docs.renovatebot.com/renovate-schema.json",
            "baseBranches": ["main"],
            "packageRules": [{"matchBaseBranches": ["main"], "extends": [ref("default"), ref("rancher-main")]}],
        }
        result = s.sync_config(cfg, ctx())
        self.assertEqual(list(result.config), ["$schema", "extends", "baseBranchPatterns", "packageRules"])
        self.assertEqual(result.config["extends"], [ref("default")])
        self.assertEqual(result.config["packageRules"][0]["extends"], [ref("rancher-main")])

    def test_bare_default_made_explicit(self):
        cfg = {"extends": ["github>rancher/renovate-config#main"], "packageRules": [rule("main", "rancher-main", "main")]}
        result = s.sync_config(cfg, ctx())
        self.assertEqual(result.config["extends"], [ref("default", "main")])
        self.assertIn("Reference the `default` preset as `github>rancher/renovate-config//default#main`.", result.changes)

    def test_default_branch_placeholder_resolved(self):
        cfg = {
            "extends": [ref("default")],
            "baseBranchPatterns": ["$default", "master", "release/v2.15"],
            "packageRules": [rule("$default", "rancher-main"), rule("release/v2.15", "rancher-2.15")],
        }
        result = s.sync_config(cfg, ctx(default="master", branches={"release/v2.15"}, cat=catalog(main=range(12, 16))))
        self.assertEqual(result.config["baseBranchPatterns"], ["master", "release/v2.15"])
        self.assertEqual(branch_rules(result.config)["master"], "rancher-main#release")
        self.assertIn("Replace `$default` with the default branch `master`.", result.changes)

    def test_foreign_config_untouched(self):
        cfg = {"extends": ["config:best-practices"], "baseBranches": ["master"]}
        result = s.sync_config(cfg, ctx())
        self.assertEqual(result.config, cfg)
        self.assertEqual(result.ref_label, "none")

    def test_custom_rule_of_eol_branch_removed(self):
        cfg = {
            "extends": [ref("default")],
            "baseBranchPatterns": ["main", "release/v2.11"],
            "packageRules": [
                rule("main", "rancher-main"),
                rule("release/v2.11", "rancher-2.11"),
                {"description": "Pin x", "matchBaseBranches": ["release/v2.11"], "matchPackageNames": ["x"], "enabled": False},
            ],
        }
        result = s.sync_config(cfg, ctx(branches={"release/v2.11"}))
        self.assertEqual(result.config["baseBranchPatterns"], ["main"])
        self.assertEqual(result.config["packageRules"], [rule("main", "rancher-main")])
        self.assertEqual(result.changes, [
            "Remove EOL preset `rancher-2.11` and branch `release/v2.11`.",
            "Remove custom rule that only applied to removed branches: Pin x",
        ])

    def test_eol_default_branch_kept(self):
        cfg = {"extends": [ref("default")], "packageRules": [rule("release/v1.0", "rancher-2.11")]}
        result = s.sync_config(cfg, ctx(default="release/v1.0"))
        self.assertEqual(result.config["packageRules"], [])
        self.assertIn("Default branch `release/v1.0` used an EOL preset; branch kept, review manually.", result.warnings)

    def test_missing_preset_warned(self):
        result = s.sync_config({"extends": [ref("default"), ref("nope")]}, ctx(populate=False))
        self.assertEqual(result.warnings, ["Preset `nope` does not exist on `#release`."])


class PopulateTest(unittest.TestCase):
    OLD = {"extends": [ref("default")], "baseBranches": ["main"], "prHourlyLimit": 2}
    TEMPLATE = "Apply Rancher {version} update constraints to branch {branch}."

    def test_identity_mapping(self):
        branches = {"release/v2.15", "release/v2.14", "release/v2.11", "release/v2.15.1"}
        result = s.sync_config(self.OLD, ctx(branches=branches, template_description=self.TEMPLATE))
        cfg = result.config
        self.assertEqual(cfg["baseBranchPatterns"], ["main", "release/v2.15", "release/v2.14"])
        self.assertEqual(cfg["packageRules"][0], rule("main", "rancher-main"))
        self.assertEqual(
            cfg["packageRules"][1],
            rule("release/v2.15", "rancher-2.15", description="Apply Rancher 2.15 update constraints to branch release/v2.15."),
        )
        self.assertEqual(len(cfg["packageRules"]), 3)

    def test_oracle_mapping(self):
        oracle = {"foo": [(0, 7, 12), (0, 7, 13), (0, 8, 14), (0, 9, 15)]}
        branches = {"release/v0.9", "release/v0.8", "release/v0.7", "release/v0.6"}
        result = s.sync_config(self.OLD, ctx(branches=branches, oracle=oracle))
        rules = branch_rules(result.config)
        self.assertEqual(rules, {
            "main": "rancher-main#release",
            "release/v0.9": "rancher-2.15#release",
            "release/v0.8": "rancher-2.14#release",
            "release/v0.7": "rancher-2.13#release",
        })

    def test_skips(self):
        cases = [
            (ctx(branches={"feature"}), "Not populated: no version branches."),
            (ctx(branches={"release/v1.0"}), "Not populated: Rancher version of the branches is unknown."),
            (ctx(default="release/v2.15"), "Not populated: default branch `release/v2.15` is a version branch; add presets manually."),
        ]
        for context, warning in cases:
            with self.subTest(warning=warning):
                result = s.sync_config(self.OLD, context)
                self.assertIn(warning, result.warnings)
                self.assertNotIn("packageRules", result.config)

    def test_custom_rules_only_suggested(self):
        cfg = {**self.OLD, "packageRules": [{"matchPackageNames": ["x"], "enabled": False}]}
        result = s.sync_config(cfg, ctx(branches={"release/v2.15"}))
        self.assertEqual(len(result.config["packageRules"]), 1)
        self.assertTrue(result.warnings[0].startswith("Not populated: config has custom package rules; suggested presets"))

    def test_only_rancher_org(self):
        result = s.sync_config(self.OLD, ctx(repo="other/foo", branches={"release/v2.15"}))
        self.assertNotIn("packageRules", result.config)

    def test_disabled(self):
        result = s.sync_config(self.OLD, ctx(branches={"release/v2.15"}, populate=False))
        self.assertNotIn("packageRules", result.config)


class CatalogTest(unittest.TestCase):
    def test_status(self):
        cat = catalog(main=range(12, 17), release=range(11, 16), eol=(9, 10))
        cat.files["main"]["rancher-2.11"] = EOL
        self.assertEqual(cat.status("main", "rancher-2.11"), "eol")
        self.assertEqual(cat.status("release", "rancher-2.11"), "eol", "EOL on main applies to release too")
        self.assertEqual(cat.status("release", "rancher-2.16"), "unreleased")
        self.assertEqual(cat.status("release", "rancher-2.8"), "eol")
        self.assertEqual(cat.status("release", "rancher-2.15"), "ok")
        self.assertEqual(cat.status("release", "nope"), "missing")
        self.assertEqual(cat.supported("release"), [12, 13, 14, 15])

class MappingTest(unittest.TestCase):
    def test_oracle_respects_pins(self):
        entries = [(0, 7, 12), (0, 7, 13), (0, 8, 14), (0, 9, 15)]
        mapping, conflict = s.oracle_mapping(entries, {"release/v0.6", "release/v0.7", "release/v0.8", "release/v0.9"})
        self.assertIsNone(conflict)
        self.assertEqual(mapping.branch_for(15), "release/v0.9")
        self.assertEqual(mapping.branch_for(13), "release/v0.7")
        self.assertIsNone(mapping.branch_for(12), "2.12 pins v0.7, not the extrapolated v0.6")
        self.assertEqual(mapping.branch_for(16), "release/v0.10")

    def test_inconsistent_offset_keeps_explicit_pairs(self):
        mapping, conflict = s.mapping_from_pairs([("release/v0.15", 14), ("release/v0.14", 12)], "test")
        self.assertEqual(conflict, "branches do not follow one version offset (`release/v0.14`, `release/v0.15`)")
        self.assertEqual(mapping.branch_for(14), "release/v0.15")
        self.assertIsNone(mapping.branch_for(16))


class OracleTest(unittest.TestCase):
    def test_parse_pins(self):
        go_mod = "\n".join([
            "require (",
            "\tgithub.com/rancher/fleet/pkg/apis v0.15.2",
            "\tgithub.com/rancher/wrangler/v3 v3.3.1",
            "\tgithub.com/rancher/lasso v0.2.3 // indirect",
            "\tgithub.com/rancher/norman v0.7.1-0.20250101000000-0123456789ab",
            ")",
        ])
        build_yaml = "fleetVersion: 108.0.0+up0.14.0\nwebhookVersion: 108.0.1+up0.9.1\n"
        dockerfile = "ENV CATTLE_CSP_ADAPTER_MIN_VERSION=v8.0.0\nENV CATTLE_REMOTEDIALER_PROXY_VERSION=v0.6.2\nFROM rancher/kubectl:v1.34.2\n"
        pins = s.parse_pins(go_mod, build_yaml, dockerfile)
        self.assertEqual(pins["fleet"], (0, 14), "build.yaml wins over go.mod")
        self.assertEqual(pins["webhook"], (0, 9))
        self.assertEqual(pins["wrangler"], (3, 3))
        self.assertEqual(pins["remotedialerproxy"], (0, 6))
        self.assertEqual(pins["kubectl"], (1, 34))
        self.assertNotIn("lasso", pins)
        self.assertNotIn("norman", pins)


class ReportTest(unittest.TestCase):
    def test_custom_percentage(self):
        cfg = {
            "$schema": "x",
            "extends": [ref("default")],
            "baseBranchPatterns": ["main", "release/v2.15"],
            "packageRules": [rule("main", "rancher-main"), rule("release/v2.15", "rancher-2.15")],
        }
        self.assertEqual(s.custom_percentage(cfg), 0)
        cfg["prHourlyLimit"] = 2
        self.assertEqual(s.custom_percentage(cfg), 11)

    def test_majors_allowed(self):
        cfg = {
            "baseBranchPatterns": ["$default", "release/v2.15", "release/v1.0", "stable"],
            "packageRules": [
                rule("main", "rancher-main"),
                rule("release/v2.15", "rancher-2.15"),
                {"matchBaseBranches": ["/^release\\//"], "matchUpdateTypes": ["major"], "enabled": False},
            ],
        }
        self.assertEqual(s.majors_allowed(cfg, "main"), ["main", "stable"])
        self.assertEqual(s.majors_allowed({**cfg, "enabled": False}, "main"), [])

    def test_automerge_mode(self):
        auto = {"matchUpdateTypes": ["patch"], "automerge": True}
        self.assertEqual(s.automerge_mode({}), "no")
        self.assertEqual(s.automerge_mode({"extends": [ref("automerge")]}), "preset (`automerge.json`)")
        self.assertEqual(s.automerge_mode({"packageRules": [auto]}), "custom")
        self.assertEqual(s.automerge_mode({"extends": [ref("automerge")], "packageRules": [auto]}), "preset + custom")

    def test_summary(self):
        reports = [
            s.RepoReport("rancher/a", "changed", "yes", "release", "https://github.com/rancher/a/pull/7", "`main`", "no", "0%", ["Did x."]),
            s.RepoReport("rancher/b", "no-config", "no Renovate config", dependabot=True),
            s.RepoReport("rancher/c", "unchanged", "no (`enabled: false`)", "main", "no change", "none", "no", "50%", dependabot=True),
            s.RepoReport("rancher/d", "no-config", "no Renovate config"),
        ]
        reports[0].diff = "--- a\n+++ b\n-x\n+y\n"
        text = s.render_summary("rancher", False, reports)
        self.assertIn("| [rancher/a](https://github.com/rancher/a) | yes | release | [#7](https://github.com/rancher/a/pull/7) |", text)
        self.assertIn("- Did x.", text)
        self.assertIn("````diff\n--- a\n+++ b\n-x\n+y\n````", text)
        self.assertIn("2 without `.github/renovate.json` (1 with Dependabot)", text)
        self.assertIn("- `rancher/b`: no Renovate config", text)
        self.assertIn("- `rancher/c`: no (`enabled: false`)", text)
        self.assertIn("neither Renovate nor Dependabot (1)</summary>\n\n- `rancher/d`\n", text)
        short = s.render_summary("rancher", False, reports, diffs=False)
        self.assertNotIn("````diff", short)
        self.assertIn("see the `renovate-config-sync` artifact", short)

    def test_pr_body(self):
        body = s.pr_body(s.SyncResult({}, ["Did x."], ["Check y."], ["Note z."]), "https://run/1", "octocat")
        self.assertIn("Changes:\n- Did x.\n\nNot changed automatically, please review:\n- Check y.\n\nNotes:\n- Note z.\n", body)
        self.assertTrue(body.endswith("Created by the [renovate-sync workflow](https://run/1), started by @octocat.\n"))


class RenderTest(unittest.TestCase):
    def roundtrip(self, text):
        return s.render_json(json.loads(text), s.JsonStyle.detect(text))

    def test_preserves_layout(self):
        text = (
            '{\n    "a": ["x", "y"],\n    "b": [\n        "x",\n        "y"\n    ],\n'
            '    "c": {"enabled": true},\n    "d": [\n        {"e": ["x", "y"]}\n    ]\n}'
        )
        self.assertEqual(self.roundtrip(text), text)

    def test_new_lists_follow_key_style(self):
        text = '{\n  "packageRules": [\n    {\n      "matchBaseBranches": ["main"]\n    }\n  ]\n}\n'
        cfg = json.loads(text)
        cfg["packageRules"].append({"matchBaseBranches": ["release/v1.0"], "extends": ["x"]})
        out = s.render_json(cfg, s.JsonStyle.detect(text))
        self.assertIn('"matchBaseBranches": ["release/v1.0"]', out)
        self.assertIn('"extends": ["x"]', out)

    def test_default_style(self):
        self.assertEqual(s.render_json({"a": ["x"], "b": {}}), '{\n  "a": [\n    "x"\n  ],\n  "b": {}\n}\n')


class FakeGitHub:
    """Serves one repository's files; Actions API access is denied unless `known`."""

    def __init__(self, files, auto_merge=None, known=False):
        self.files = files
        self.auto_merge = auto_merge
        self.known = known

    def request(self, method, path, body=None, raw=False, allow_missing=False):
        if path.endswith("/contents/.github"):
            return [{"name": p.split("/")[1]} for p in self.files if p.count("/") == 1]
        if path.endswith("/contents/.github/workflows"):
            return [{"name": p.rsplit("/", 1)[1]} for p in self.files if p.startswith(".github/workflows/")]
        if "/actions/workflows" in path:
            if not self.known:
                return None
            return {"workflows": [{"path": p, "state": "active"} for p in self.files if p.startswith(".github/workflows/") and "renovate" in p.lower()]}
        if "/branches/" in path:
            return {"protection": {"required_status_checks": {"enforcement_level": "everyone", "contexts": ["test"]}}}
        raise AssertionError(f"unexpected {method} {path}")

    def graphql(self, query, **variables):
        assert variables == {"owner": "rancher", "name": "foo"}, variables
        return {"repository": {"autoMergeAllowed": self.auto_merge}}

    def file(self, repo, path, ref=None):
        return self.files.get(path)

    def branches(self, repo):
        return {"main"}


WORKFLOW = "uses: rancher/renovate-config/.github/workflows/renovate-vault.yml@abc # v1.0.13\n"


def run_process(files, auto_merge=None, known=False, **shared):
    args = argparse.Namespace(populate=False, out=None, dry_run=True, run_url=None, actor=None)
    shared = s.Shared(catalog(), workflow=WORKFLOW, release="v1.0.13", **shared)
    gh = FakeGitHub(files, auto_merge, known)
    return s.process_repo(gh, {"full_name": "rancher/foo", "default_branch": "main"}, args, shared)


class ProcessRepoTest(unittest.TestCase):
    CONFIG = '{\n  "extends": ["github>rancher/renovate-config//default#release"]\n}\n'

    def run_repo(self, files):
        return run_process({s.CONFIG_PATH: self.CONFIG, **files})

    def test_invalid_json(self):
        report = run_process({s.CONFIG_PATH: "{"})
        self.assertEqual(report.status, "invalid")
        self.assertTrue(report.warnings[0].startswith("Invalid JSON: "))

    def test_replaces_obsolete_workflow(self):
        report = self.run_repo({".github/workflows/renovate.yml": "old\n"})
        self.assertEqual(report.status, "changed")
        self.assertIn("Replace obsolete `.github/workflows/renovate.yml` with `.github/workflows/renovate-vault.yml` pinned to `v1.0.13`.", report.changes)
        self.assertIn("--- a/.github/workflows/renovate.yml\n+++ /dev/null\n", report.diff)
        self.assertIn("--- /dev/null\n+++ b/.github/workflows/renovate-vault.yml\n", report.diff)

    def test_updates_outdated_workflow(self):
        report = self.run_repo({s.WORKFLOW_PATH: "old\n"})
        self.assertEqual(report.changes, ["Update `.github/workflows/renovate-vault.yml` to the current template pinned to `v1.0.13`."])

    def test_current_workflow_unchanged(self):
        report = self.run_repo({s.WORKFLOW_PATH: WORKFLOW})
        self.assertEqual((report.status, report.changes), ("unchanged", []))

    def test_missing_workflow_not_added(self):
        report = self.run_repo({})
        self.assertEqual(report.status, "unchanged")
        self.assertIn("No Renovate workflow found; none added.", report.notes)

    def test_workflow_kept_for_foreign_config(self):
        report = run_process({s.CONFIG_PATH: '{\n  "extends": ["config:recommended"]\n}\n', ".github/workflows/renovate.yml": "old\n"})
        self.assertEqual(report.status, "unchanged")
        self.assertIn(f"Does not use {s.PRESET_REPO} presets; left unchanged.", report.notes)


class AutomergeTest(unittest.TestCase):
    CONFIG = json.dumps({
        "extends": [ref("default")],
        "baseBranchPatterns": ["main", "release/v1.1"],
        "packageRules": [rule("main", "rancher-main"), rule("release/v1.1", "rancher-2.15")],
    })
    APPROVE = "token OWNER--REPOSITORY--auto-approve\n"

    def run_repo(self, auto_merge=True, files=None, config=None, known=True):
        return run_process(
            {s.CONFIG_PATH: config or self.CONFIG, s.WORKFLOW_PATH: WORKFLOW, **(files or {})},
            auto_merge,
            known=known,
            approve=self.APPROVE,
        )

    def test_enables_automerge_and_approve_workflow(self):
        report = self.run_repo()
        self.assertEqual(report.automerge, "preset (`automerge.json`); approve: Vault token (added)")
        self.assertIn('+      "extends": ["github>rancher/renovate-config//automerge#release"]', report.diff)
        self.assertIn("+++ b/.github/workflows/auto-approve-bot-prs.yml\n@@ -0,0 +1 @@\n+token rancher--foo--auto-approve\n", report.diff)
        self.assertTrue(any("github/token/rancher--foo--auto-approve--pull_requests--write" in n for n in report.notes))

    def test_existing_approve_workflow_kept(self):
        report = self.run_repo(files={".github/workflows/auto-approve-bot-prs.yml": "create-github-app-token\n"})
        self.assertEqual(report.automerge, "preset (`automerge.json`); approve: App credentials")
        self.assertNotIn("auto-approve-bot-prs.yml", report.diff)

    def test_blockers(self):
        report = self.run_repo(auto_merge=False)
        self.assertIn("Automerge not enabled: auto-merge is disabled in the repository settings.", report.notes)
        report = self.run_repo(auto_merge=None)
        self.assertIn("Automerge not enabled: the auto-merge repository setting is not readable.", report.notes)

    def test_existing_automerge_kept(self):
        cfg = json.loads(self.CONFIG)
        cfg["packageRules"].append({"matchBaseBranches": ["main"], "extends": [ref("automerge")]})
        report = self.run_repo(config=json.dumps(cfg))
        self.assertEqual((report.status, report.automerge), ("unchanged", "preset (`automerge.json`)"))

    def test_unknown_workflow_state_blocks(self):
        report = self.run_repo(known=False)
        self.assertEqual(report.automerge, "no")
        self.assertIn("Automerge not enabled: Renovate activity is unconfirmed (`yes (workflow state unknown)`).", report.notes)


class SecurityMirrorTest(unittest.TestCase):
    def test_skips_security_repo_with_base(self):
        repos = [{"name": n} for n in ("fleet", "fleet-security", "rancher-security", "foo-security-tools")]
        self.assertEqual([r["name"] for r in s.skip_security_mirrors(repos)], ["fleet", "rancher-security", "foo-security-tools"])


class PrGitHub:
    """Git data and pull request endpoints of `rancher/foo` used by `open_sync_pr`."""

    def __init__(self, branch=None, pr=None):
        self.branch, self.pr, self.writes = branch, pr, []

    def file(self, repo, path, ref=None):
        return self.branch.get(path)

    def request(self, method, path, body=None, raw=False, allow_missing=False):
        path = path.removeprefix("repos/rancher/foo/")
        if method != "GET":
            self.writes.append(f"{method} {path}")
        if path == "git/ref/heads/main":
            return {"object": {"sha": "base"}}
        if path == f"git/ref/heads/{s.SYNC_BRANCH}":
            return self.branch is not None or None
        if path.startswith("pulls?state=open"):
            return [self.pr] if self.pr else []
        if path.startswith("pulls"):
            self.pr = {"number": 1, "html_url": "https://github.com/rancher/foo/pull/1", **(self.pr or {}), **body}
            return self.pr
        return {"sha": "new", "tree": {"sha": "tree"}}


class OpenSyncPrTest(unittest.TestCase):
    UPDATES = {s.CONFIG_PATH: "{}\n"}
    BODY = "Changes:\n- a\n\nCreated by the [renovate-sync workflow](https://x/1), started by @me.\n"
    PR = {"number": 7, "html_url": "https://github.com/rancher/foo/pull/7", "title": s.PR_TITLE, "body": BODY}

    def writes(self, branch=None, pr=None):
        gh = PrGitHub(branch, pr)
        url = s.open_sync_pr(gh, "rancher/foo", "main", self.UPDATES, self.BODY)
        self.assertEqual(url, gh.pr["html_url"])
        return gh.writes

    def test_creates_branch_and_pr(self):
        self.assertEqual(self.writes(), ["POST git/trees", "POST git/commits", "POST git/refs", "POST pulls"])

    def test_current_branch_and_pr_untouched(self):
        pr = {**self.PR, "body": self.BODY.replace("https://x/1", "https://x/0")}
        self.assertEqual(self.writes(self.UPDATES, pr), [])

    def test_stale_branch_force_pushed(self):
        self.assertEqual(self.writes({}, self.PR), ["POST git/trees", "POST git/commits", f"PATCH git/refs/heads/{s.SYNC_BRANCH}"])

    def test_changed_body_updated_without_push(self):
        self.assertEqual(self.writes(self.UPDATES, {**self.PR, "body": "old"}), ["PATCH pulls/7"])


if __name__ == "__main__":
    unittest.main()
