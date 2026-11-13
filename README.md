# renovate-config

This repository contains the centralized Renovate preset (`default.json`) and the reusable workflow used to run Renovate (`.github/workflows/renovate-vault.yml`)

## Used branches

- `main` (aka develop), this is where the development happens and changes are introduced. This branch is kept up-to-date with Renovate pointing to its own preset.
- `release`, this is the branch where all the repositories point to. After testing our changes in `main`, we promote `main` to `release`.

## Dependency bump alignment across Rancher Manager

For easier alignment of versions across projects around the Rancher Manager
ecosystem, a few presets were created that enforce version constraints for each
Rancher minor version. These presets mostly restrict bumps to security-related updates, with the exception of Go bumps and patch-level Kubernetes and K3s bumps.

The presets are available at the root of this repository and follow the naming
convention: `rancher-<version>.json`. To use these presets, a project can configure
their `renovate.json` as per below:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>rancher/renovate-config//default#release"],
  "baseBranchPatterns": [
    "main",
    "release/v0.17",
    "release/v0.16"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["main"],
      "extends": ["github>rancher/renovate-config//rancher-main#release"]
    },
    {
      "matchBaseBranches": ["release/v0.17"],
      "extends": ["github>rancher/renovate-config//rancher-2.16#release"]
    },
    {
      "matchBaseBranches": ["release/v0.16"],
      "extends": ["github>rancher/renovate-config//rancher-2.15#release"]
    }
  ]
}
```

When using the `#main` presets, extend `default#main` once at top level. Do not add `default` to each branch preset. The release workflow rewrites `#main` to `#release` and adds the root preset to `rancher-*.json` for older consumers that do not yet have a top-level extension.

The `rancher-main.json` preset contains additional configuration for the
`rancher/rancher` main branch and its subprojects, like providing Go and Kubernetes version restrictions, but not much more than that.
Other repositories can skip the following `main` package rule that extends `rancher-main`:

```json
{
  "matchBaseBranches": ["main"],
  "extends": ["github>rancher/renovate-config//rancher-main#release"]
}
```

Branches that are part of `baseBranchPatterns` and do not have a dedicated `packageRule` will get almost all updates Renovate provides, including major bumps.
That is also true for `rancher-main` besides the restrictions mentioned above.

## Labels

`default.json` labels every PR with `dependencies` and adds `dependency/major` to branches that contain at least one major bump, so major updates can be found with `label:dependency/major`.

Labels are per repository. GitHub creates a missing label on first use, but without a color or description, so projects that want a consistent `dependency/major` search should create it once:

```bash
gh label create dependency/major --color b60205 --description "Major version bump of a dependency (breaking change)"
```

Renovate only syncs labels of PRs whose body contains the `labels` entry in its `renovate-debug` block, which every PR created since Renovate 37 does. PRs that were relabeled manually are left untouched, and the repository cache can skip unchanged PRs; tick the rebase/retry checkbox of such a PR to let Renovate sync its labels.

## Opting into automerge

Projects can opt into automerging eligible patch and minor updates by extending
the repository-agnostic `automerge.json` preset in a separate branch-scoped
`extends` entry after the release preset entries:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>rancher/renovate-config//default#release"],
  "baseBranchPatterns": [
    "main",
    "release/v0.17",
    "release/v0.16"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["main"],
      "extends": ["github>rancher/renovate-config//rancher-main#release"]
    },
    {
      "matchBaseBranches": ["release/v0.17"],
      "extends": ["github>rancher/renovate-config//rancher-2.16#release"]
    },
    {
      "matchBaseBranches": ["release/v0.16"],
      "extends": ["github>rancher/renovate-config//rancher-2.15#release"]
    },
    {
      "matchBaseBranches": [
        "main",
        "release/v0.17",
        "release/v0.16"
      ],
      "extends": ["github>rancher/renovate-config//automerge#release"]
    }
  ]
}
```

Keep the release preset entries and automerge entry separate because Renovate
flattens nested package rules. The preset does not enable updates or change
allowed versions.

For a consumer tracking `main`, use `#main` for all presets and extend
`default#main` once at top level; add `automerge#main` as a separate top-level
extension when automerge is desired.

### GitHub requirements for automerge

Enforce successful CI status with branch protection rules so GitHub automerge
waits for CI to complete successfully before merging.

- Enable auto-merge for the repository in GitHub.
- Add the `renovate-rancher` app user to `Restrict who can push to matching branches` in `Branch protection rules` for the main and release branches.
- Require an approving review, either from the [auto-approve workflow](files/auto-approve-bot-prs.yml) or by not requiring approvals.

The [auto-approve workflow](files/auto-approve-bot-prs.yml) approves a pull request only when it has an auto-merge request enabled by the configured Renovate bot, comes from that bot in the same repository, uses a `renovate/*` head branch, has met the configured minimum working-day age (`RENOVATE_AUTO_APPROVE_MIN_WORKING_DAYS`, default 1), and all checks pass or are skipped. Copy it to `.github/workflows/auto-approve-bot-prs.yml` and replace `OWNER--REPOSITORY` with the repository, for example `rancher--fleet-helm-charts`. The approval token is read from Vault at `github/token/<owner>--<repository>--auto-approve--pull_requests--write`; that role has to be provisioned for the repository before the workflow can approve.

### Fine-grained automerge configuration

To enable security automerge only without using the shared preset, use this
branch-scoped configuration:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>rancher/renovate-config//default#release"],
  "baseBranchPatterns": [
    "main",
    "release/v0.17",
    "release/v0.16"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["main"],
      "extends": ["github>rancher/renovate-config//rancher-main#release"]
    },
    {
      "matchBaseBranches": ["release/v0.17"],
      "extends": ["github>rancher/renovate-config//rancher-2.16#release"]
    },
    {
      "matchBaseBranches": ["release/v0.16"],
      "extends": ["github>rancher/renovate-config//rancher-2.15#release"]
    },
    {
      "matchBaseBranches": [
        "main",
        "release/v0.17",
        "release/v0.16"
      ],
      "description": "Automerge eligible security updates",
      "matchDepTypes": ["!devDependencies", "!dev-dependencies", "!test"],
      "matchJsonata": [
        "$exists(vulnerabilityFixVersion) or $exists(isVulnerabilityAlert)"
      ],
      "matchUpdateTypes": ["patch", "minor"],
      "automerge": true
    }
  ]
}
```

## Go generation dependencies

Renovate runs `go generate ./...` after Go module updates to refresh tracked generated files in its PRs. It aborts the whole artifact update when a generation step needs a tool that is missing from the Renovate container, which leaves the update PR without its `go.sum` changes. Only the Go toolchain and the tools declared by the repository are guaranteed to be present, so remove any other tool the generation step relies on.

### Declare post-processing in the API types

A generation step that only rewrites the generated output usually can be replaced by controller-gen itself. [rancher/k3k](https://github.com/rancher/k3k) ran `controller-gen` and then a `yq` loop adding an annotation to every generated CRD; declaring the annotation on the type removes the loop and the `yq` dependency with it:

```go
// +kubebuilder:object:root=true
// +kubebuilder:metadata:annotations="helm.sh/resource-policy=keep"
type Cluster struct {
```

See [rancher/k3k#1308](https://github.com/rancher/k3k/pull/1308).

### Pin tools in tool modules

Tools that are not part of the Go toolchain can be declared in their own module and run through it, which removes the need for a binary on `PATH` and keeps the version in a `go.mod` file that Renovate updates. [rancher/fleet](https://github.com/rancher/fleet) uses this for `mockgen` and `controller-gen`:

```sh
go tool -modfile gotools/mockgen/go.mod mockgen --version
```

Ordering generated CRDs no longer needs `yq` either, because controller-gen writes one file per CRD under a name that sorts into the required order, so concatenating them is enough:

```sh
${CONTROLLERGEN} crd webhook paths="./pkg/apis/..." output:crd:dir="${tmpdir}/crds"
cat "${tmpdir}"/crds/*.yaml > "$CRDS_YAML"
```

See [rancher/fleet#5889](https://github.com/rancher/fleet/pull/5889).

### Disabling the option

When the generation step cannot be made self-contained, turn the option off. Set `postUpdateOptions` to `null` at the top level and re-add the options you want to keep in a `packageRules` entry:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>rancher/renovate-config//default#release"],
  "postUpdateOptions": null,
  "packageRules": [
    {
      "description": "Keep go mod tidy, but do not run go generate",
      "matchManagers": ["gomod"],
      "postUpdateOptions": ["gomodTidyAll", "gomodUpdateImportPaths"]
    }
  ]
}
```

## Callers of the removed `renovate.yml`

The deprecated reusable workflow `.github/workflows/renovate.yml` was deleted. Callers that still reference `rancher/renovate-config/.github/workflows/renovate.yml@<ref>` fail at workflow startup (`error parsing called workflow` / workflow file issue), and because the failure is a parse error no job starts, so Renovate stops for that repository until the caller is updated. Callers pinned to a commit SHA that contained the file keep working until they move the pin.

To migrate, replace the caller with the current `renovate-vault.yml` as described in [Keeping `renovate-vault.yml` up to date in consuming repositories](#keeping-renovate-vaultyml-up-to-date-in-consuming-repositories), run it once manually to verify the token flow, and then delete the old caller. Note the differences: `renovate-vault.yml` needs `id-token: write` and reads its token from Vault instead of the `RENOVATE_APP_ID`/`RENOVATE_PRIVATE_KEY` secrets, and its `logLevel` input is unused because logging is always at `debug` level.

## Keeping `renovate-vault.yml` up to date in consuming repositories

Renovate only bumps the pinned `uses: rancher/renovate-config/.github/workflows/renovate-vault.yml@<sha> # <tag>` reference in consuming repositories. It does not update the contents of the workflow file itself, so the copy in a consuming repository can become outdated (for example, missing newer inputs or permissions).

The `RENOVATE_VERSION` inside `renovate-vault.yml` is handled by the `renovate-bumps` rule in `default.json`. Because Renovate publishes several releases per day, that rule allows a bump only on Tuesdays and only to a release that is at least 14 days old.

Every change of the reusable workflow needs a `v1.0.x` tag, because the deploy and sync workflows render pins from the highest tag, and a pin comment is only truthful while that tag points at the pinned commit. The [Tag release](.github/workflows/tag-release.yml) workflow creates the next patch tag on any push to `main` that touches `.github/workflows/renovate-vault.yml`, and skips the tag when that file is unchanged since the last one. A daily scheduled run retries a tag push that a failed run left behind. Consumers then pick the tag up through Renovate, which tracks `rancher/renovate-config` with zero release age and a priority boost. A direct edit of `renovate-vault.yml` on `release` gets no tag, as `release` only receives promoted `main` commits for that file.

Verify that `.github/workflows/renovate-vault.yml` in each consuming repository is up to date, and use one of the following options:

- Run the [Deploy Renovate](.github/workflows/renovate-deploy.yml) workflow against the repository. It opens a PR that replaces `renovate-vault.yml` with the current version.
- If the deploy workflow cannot be used, copy the file from an up-to-date consumer such as [rancher/rancher](https://github.com/rancher/rancher/blob/main/.github/workflows/renovate-vault.yml).

## Overriding `minimumReleaseAge` for a single run

The `Renovate` workflow of a consuming repository takes one optional input for a manual run, `refreshGroup`, which ignores `minimumReleaseAge` for the selected scope in that run: `all` for every dependency, or `go`, `kubernetes`, `security` and `github-actions` for one update group.

A selection accepts any release, which the presets express as `minimumReleaseAge: "0 days"`; the workflow fails the run for an unknown group instead of ignoring it. An empty selection applies the configured release ages. Scheduled runs ignore the input. The reusable `renovate-vault.yml` still accepts the deprecated `minimumReleaseAge` input for callers that set it; a selection wins over it and the run logs that it is ignored.

`go` covers the `go.mod` and `toolchain` directives, the `SETUP_GO_VERSION`, `GOLANG_VERSION` and `GOTOOLCHAIN` workflow values, the `ghcr.io/rancher/ci-image/go*` workflow images, `Dockerfile` Go images including the BCI and AppCo Go images, `golangci-lint` and its module paths, and `rancher/hardened-build-base`. It applies the same package list as the Go toolchain preset. The toolchain and the images usually need the same version, so selecting `go` keeps them on one branch. `kubernetes` covers the `k8s.io/` and `sigs.k8s.io/` modules, the `wrangler` and `lasso` modules, `rancher/kubectl` and `rancher/k3s`, `kubernetes/kubernetes` with its architecture specific checksum entries, and `k3s-io/k3s`. `security` covers dependencies that carry a vulnerability fix. `github-actions` covers the Actions manager.

`all` is passed as `RENOVATE_FORCE.minimumReleaseAge`, so it wins over every rule of the repository config and of its presets. A scoped override cannot use that field: `force` replaces the resolved `packageRules` instead of extending them, so a rule placed there would drop the constraints the presets and the repository define. The workflow therefore passes `force.description` holding a `renovate-minimum-release-age-group:<group>` marker, and `default.json` matches that marker in rules that accept any release. Those rules also set a nested `force`, because the vulnerability alert configuration carries its own `force` that would otherwise restore the configured release age for exactly the security bumps these runs are usually about. The repository config is not modified.

## Keeping consumer `renovate.json` presets up to date

The [Sync Renovate configs](.github/workflows/renovate-sync.yml) workflow brings `.github/renovate.json` in consuming repositories up to the current Rancher presets while keeping their local customizations, and opens the result as a `renovate-config-sync` PR per changed repository. Repeated runs are idempotent: a sync branch that already holds the proposal and its up-to-date PR are left alone, and the branch of a merged sync PR is deleted. Run it manually with an organization (`rancher`) or a single repository (`rancher/fleet`) as `target`; `dryRun` (the default) only writes the report to the job summary and uploads the proposed files as the `renovate-config-sync` artifact. The rules it applies are documented on the functions in [sync_renovate_configs.py](.github/scripts/sync_renovate_configs.py) and covered by [test_sync_renovate_configs.py](.github/scripts/test_sync_renovate_configs.py); to run it locally use `GH_TOKEN=$(gh auth token) python3 .github/scripts/sync_renovate_configs.py --target rancher/fleet --out report`, as only `--apply` opens PRs.

## Testing new changes

### Matching patterns / versions found

Renovate configuration is not very unit testing friendly. Therefore, this project aims to validate all renovate files for syntax issues via `make validate` at its PR checks.
This runs a strict check, so new formats which require migration will break.

When introducing new configuration, changes can be tested manually by adding a sample target file in `/tests` and running `make test` locally. To test the impact on a `Dockerfile` for example, create a `test/Dockerfile`:

```Dockerfile
ENV KUBECTL_VERSION v1.25.12
```

Results in:

```
DEBUG: packageFiles with updates (repository=local)
       "config": {
         "regex": [
           {
             "deps": [
               {
                 "depName": "kubernetes/kubernetes",
                 "currentValue": "v1.25.12",
                 "datasource": "github-releases",
                 "replaceString": "ENV KUBECTL_VERSION v1.25.12\n",
                 "skipReason": "github-token-required",
                 "updates": [],
                 "packageName": "kubernetes/kubernetes"
               },
               {
                 "depName": "kubernetes/kubernetes",
                 "currentValue": "v1.25.12",
                 "datasource": "github-releases",
                 "replaceString": "ARG KUBECTL_VERSION=v1.25.12\n",
                 "skipReason": "github-token-required",
                 "updates": [],
                 "packageName": "kubernetes/kubernetes"
               }
             ],
             "matchStrings": [
               "ENV KUBECTL_VERSION(\\=|\\s)(?<currentValue>.*?)\\n",
               "ARG KUBECTL_VERSION(\\=|\\s)(?<currentValue>.*?)\\n"
             ],
             "depNameTemplate": "kubernetes/kubernetes",
             "datasourceTemplate": "github-releases",
             "packageFile": "Dockerfile"
           }
         ]
       }
```

Some specific data sources will require a token (as per example above) which can be provided via the `GITHUB_COM_TOKEN` environment variable. This token requires read-only access to public repositories and it is used to fetch [changelogs] without being constraint by GH's API rate-limits.

### E2E testing
For non straight-forward changes, an effective way to confirm the
configuration is working as expected is to do a full renovate run.

#### Manually triggering the Renovate workflow
Push changes to a branch on the target repository (not your fork). On
the GitHub Actions tab, manually trigger renovate via the `run workflow`
button. Ensure that you pick the new branch and that "Override all
schedules" is set to `true`.

Note that Renovate may at times merge your branch configuration
with the configuration from the `default` branch of that repository, which
may result in your settings not working as expected.
To understand the final config, check the section `DEBUG: Post-massage config`
of the logs. The latest `renovate-vault.yml` runs Renovate at `debug` level
by default, so this section is present in every run.

#### Downloading the full log

The latest `renovate-vault.yml` always logs at `debug` level, so the `DEBUG`
sections are present in every run. Consuming repositories get debug logs once
they pin a release that includes this change. Their copy of `renovate-vault.yml`
should be current too, see
[Keeping `renovate-vault.yml` up to date](#keeping-renovate-vaultyml-up-to-date-in-consuming-repositories).

To get the full log of a run:

1. Open the run on the GitHub Actions tab and click the `renovate` job.
2. Expand the `Self-hosted Renovate` step.
3. Click the gear icon in the top right of the log and select
   "Download log archive".

The archive has one file per step with the complete output, including the
`DEBUG` sections such as `Post-massage config` that the web view cuts off.
GitHub deletes logs after the retention period, so save the file when
reporting a problem.

#### Running on Forks
The execution in forks is possible by creating the secret `RENOVATE_FORK_GH_TOKEN`
in the fork repository, then triggering the Renovate workflow.
This provides the best way to test E2E scenarios, so that you can
manipulate dependency versions across the different branches in
the fork.

[changelogs]: https://docs.renovatebot.com/getting-started/running/#githubcom-token-for-changelogs
