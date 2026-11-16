# renovate-config

This repository contains the centralized Renovate preset (`default.json`) and the reusable workflow used to run Renovate (`.github/workflows/renovate-vault.yml`)

## Used branches

- `main` (aka develop), this is where the development happens and changes are introduced. This branch is kept up-to-date with Renovate pointing to its own preset.
- `release`, this is the branch where all the repositories point to. After testing our changes in `main`, we promote `main` to `release`.

## Dependency bump alignment across Rancher Manager

For easier alignment of versions across projects around the Rancher Manager
ecosystem, a few presets were created that enforce version constraints for each
Rancher minor version. These presets mostly restrict bumps to security-related updates, with the exception of Go bumps, patch-level Kubernetes and K3s bumps, and Wrangler development versions, which are replaced by the matching final release so a release branch does not keep a `-rc` or `-beta` dependency that was current when it was cut.

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

## Keeping consuming repositories up to date

Renovate only bumps the pinned `uses: rancher/renovate-config/.github/workflows/renovate-vault.yml@<sha> # <tag>` reference in consuming repositories. The workflow file and `.github/renovate.json` themselves are not updated, so both can become outdated. Two workflows fix that and open a PR per repository:

- [Deploy Renovate](.github/workflows/renovate-deploy.yml) replaces `renovate-vault.yml` with the current version. Alternatively copy the file from an up-to-date consumer such as [rancher/rancher](https://github.com/rancher/rancher/blob/main/.github/workflows/renovate-vault.yml).
- [Sync Renovate configs](.github/workflows/renovate-sync.yml) brings `.github/renovate.json` up to the current presets while keeping local customizations, as a `renovate-config-sync` PR. Run it with an organization (`rancher`) or a single repository (`rancher/fleet`) as `target`. `dryRun` (the default) only writes the report to the job summary and uploads the proposed files as the `renovate-config-sync` artifact. The rules are documented in [sync_renovate_configs.py](.github/scripts/sync_renovate_configs.py). Locally: `GH_TOKEN=$(gh auth token) python3 .github/scripts/sync_renovate_configs.py --target rancher/fleet --out report`; only `--apply` opens PRs.

Every change of `renovate-vault.yml` gets a `v1.0.x` tag from the [Tag release](.github/workflows/tag-release.yml) workflow on push to `main`, and consumers pick it up through Renovate. `RENOVATE_VERSION` inside the workflow is bumped by the `renovate-bumps` rule in `default.json`, only on Tuesdays and only to releases at least 14 days old.

## Overriding `minimumReleaseAge` for a single run

The `Renovate` workflow of a consuming repository takes the optional manual input `refreshGroup`, which ignores `minimumReleaseAge` for the selected scope in that run. Scheduled runs ignore it, an empty value keeps the configured ages, and an unknown value fails the run.

- `all`: every dependency
- `go`: Go toolchain, `golangci-lint`, Go images (including BCI and AppCo) and `rancher/hardened-build-base`
- `kubernetes`: `k8s.io/`, `sigs.k8s.io/`, `wrangler`, `lasso`, `rancher/kubectl`, `rancher/k3s`, `kubernetes/kubernetes` and `k3s-io/k3s`
- `security`: dependencies carrying a vulnerability fix
- `github-actions`: the Actions manager

`all` is passed as `RENOVATE_FORCE.minimumReleaseAge`. Scoped groups cannot use that field because `force` replaces the resolved `packageRules`, so the workflow passes a `renovate-minimum-release-age-group:<group>` marker in `force.description` that rules in `default.json` match (`minimumReleaseAge: "0 days"`). The repository config is not modified. The deprecated `minimumReleaseAge` input of `renovate-vault.yml` is ignored when a selection is set.

## Testing new changes

### Running Renovate

For non straight-forward changes, do a full Renovate run.

#### Manually triggering the Renovate workflow

Push changes to a branch on the target repository (not your fork). On the GitHub Actions tab, trigger Renovate via the `run workflow` button, pick the new branch and set "Override all schedules" to `true`.

Manual runs are still bound by the PR limits of the configuration (by default `prHourlyLimit` 2 and `prConcurrentLimit` 10; Go updates are exempt from the hourly limit). Open Renovate PRs count against them, so expect only a few new PRs per run. Merge or close old ones, or run again later, to get the rest.

Renovate may merge your branch configuration with the configuration from the `default` branch of that repository. Check the `DEBUG: Post-massage config` section of the log for the final config.

#### Downloading the full log

`renovate-vault.yml` always logs at `debug` level, so the `DEBUG` sections are present in every run of consumers with a current copy (see [Keeping consuming repositories up to date](#keeping-consuming-repositories-up-to-date)). To get the full log:

1. Open the run on the GitHub Actions tab and click the `renovate` job.
2. Expand the `Self-hosted Renovate` step.
3. Click the gear icon in the top right of the log and select "Download log archive".

The archive has one file per step with the complete output, including `DEBUG` sections such as `Post-massage config` that the web view cuts off. GitHub deletes logs after the retention period, so save the file when reporting a problem.

#### Running on Forks

Create the secret `RENOVATE_FORK_GH_TOKEN` in the fork repository, then trigger the Renovate workflow. This allows manipulating dependency versions across branches in the fork.

### Matching patterns / versions found

`make validate` runs a strict syntax check of all Renovate files in the PR checks; formats that require migration break it. To test a regex or rule, add a sample target file in `/tests` and run `make test` locally. For example, `test/Dockerfile` with:

```Dockerfile
ENV KUBECTL_VERSION v1.25.12
```

lists the detected dependency (`depName: kubernetes/kubernetes`, `currentValue: v1.25.12`) under `DEBUG: packageFiles with updates`. Datasources that need a token, as here, read it from `GITHUB_COM_TOKEN` (read-only access to public repositories is enough).