# renovate-config

This repository contains the centralized Renovate preset (`default.json`) and the reusable workflow used to run Renovate (`.github/workflows/renovate.yml`)

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
    "release/v0.16",
    "release/v0.15"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["main"],
      "extends": ["github>rancher/renovate-config//rancher-main#release"]
    },
    {
      "matchBaseBranches": ["release/v0.16"],
      "extends": ["github>rancher/renovate-config//rancher-2.15#release"]
    },
    {
      "matchBaseBranches": ["release/v0.15"],
      "extends": ["github>rancher/renovate-config//rancher-2.14#release"]
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
    "release/v0.16",
    "release/v0.15"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["main"],
      "extends": ["github>rancher/renovate-config//rancher-main#release"]
    },
    {
      "matchBaseBranches": ["release/v0.16"],
      "extends": ["github>rancher/renovate-config//rancher-2.15#release"]
    },
    {
      "matchBaseBranches": ["release/v0.15"],
      "extends": ["github>rancher/renovate-config//rancher-2.14#release"]
    },
    {
      "matchBaseBranches": [
        "main",
        "release/v0.16",
        "release/v0.15"
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
- Require an approving review, either from a [GitHub Action](https://github.com/rancher/rancher/blob/main/.github/workflows/auto-approve-bot-prs.yml) or by not requiring approvals.

The [GitHub Action](https://github.com/rancher/rancher/blob/main/.github/workflows/auto-approve-bot-prs.yml) approves a pull request only when it has an auto-merge request, comes from the configured Renovate bot in the same repository, uses a `renovate/*` head branch, includes `**Automerge**: Enabled` in its body, has met the configured minimum working-day age, and all checks pass.

### Fine-grained automerge configuration

To enable security automerge only without using the shared preset, use this
branch-scoped configuration:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>rancher/renovate-config//default#release"],
  "baseBranchPatterns": [
    "main",
    "release/v0.16",
    "release/v0.15"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["main"],
      "extends": ["github>rancher/renovate-config//rancher-main#release"]
    },
    {
      "matchBaseBranches": ["release/v0.16"],
      "extends": ["github>rancher/renovate-config//rancher-2.15#release"]
    },
    {
      "matchBaseBranches": ["release/v0.15"],
      "extends": ["github>rancher/renovate-config//rancher-2.14#release"]
    },
    {
      "matchBaseBranches": [
        "main",
        "release/v0.16",
        "release/v0.15"
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
button. Ensure that you pick the new branch and that "Override default log
level" is set to `debug` and "Override all schedules" is set to `true`.

Note that Renovate may at times merge your branch configuration
with the configuration from the `default` branch of that repository, which
may result in your settings not working as expected.
To understand the final config, check the section `DEBUG: Post-massage config`
of the logs - this is only visible when renovate is executed in
debug mode as per above.

#### Running on Forks
The execution in forks is possible by creating the secret `RENOVATE_FORK_GH_TOKEN`
in the fork repository, then triggering the Renovate workflow.
This provides the best way to test E2E scenarios, so that you can
manipulate dependency versions across the different branches in
the fork.

[changelogs]: https://docs.renovatebot.com/getting-started/running/#githubcom-token-for-changelogs
