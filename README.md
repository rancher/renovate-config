# renovate-config

This repository contains the centralized Renovate preset (`default.json`) and the reusable workflow used to run Renovate (`.github/workflows/renovate.yml`)

## Used branches

- `main` (aka develop), this is where the development happens and changes are introduced. This branch is kept up-to-date with Renovate pointing to its own preset.
- `release`, this is the branch where all the repositories point to. After testing our changes in `main`, we promote `main` to `release`.

## Testing new changes

Renovate configuraiton is not very unit testing friendly. Therefore, this project aims to validate all renovate files for syntax issues via `make validate`.
This runs a strict check, so new formats which require migration will break.

New changes can be tested by adding a target file in `/tests` and running `make test`. Running it with the current sample `Dockerfile` results in:

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
