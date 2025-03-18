# renovate-config

This repository contains the centralized Renovate preset (`default.json`) and the reusable workflow used to run Renovate (`.github/workflows/renovate.yml`)

## Used branches

- `main` (aka develop), this is where the development happens and changes are introduced. This branch is kept up-to-date with Renovate pointing to its own preset.
- `release`, this is the branch where all the repositories point to. After testing our changes in `main`, we promote `main` to `release`.

## Dependency bump alignment across Rancher Manager

For easier alignment of versions across projects around the Rancher Manager
ecosystem, a few presets were created that enforce version contraints for each
Rancher minor version.

The presets are available at the root of this repository and follow the naming
convention: `rancher-<version>.json`. To use these presets, a project can configure
their `renovate.json` as per below:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "github>rancher/renovate-config//rancher-main#rancher-presets"
  ],
  "baseBranches": [
    "main"
  ],
  "packageRules": [
    {
      "matchBaseBranches": ["releases/v0.7.x"],
      "extends": ["github>rancher/renovate-config//rancher-2.11#rancher-presets"]
    },
    {
      "matchBaseBranches": ["releases/v0.6.x"],
      "extends": ["github>rancher/renovate-config//rancher-2.10#rancher-presets"]
    }
  ]
}
```

## Testing new changes

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

[changelogs]: https://docs.renovatebot.com/getting-started/running/#githubcom-token-for-changelogs
