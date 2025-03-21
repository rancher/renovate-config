#!/bin/bash

set -exo pipefail

DATA_DIR="data"

kustomize_fetch_data() {
    curl -L https://api.github.com/repos/kubernetes-sigs/kustomize/releases | \
    jq -r '.[].assets[] | select(.name == "checksums.txt") | .browser_download_url' | \
    head -n3 | xargs -I{} curl -L {} >> "${DATA_DIR}/kustomize-data.raw"
}

kustomize_save_arch_sources() {
    archs=("amd64" "arm64" "s390x")

    for arch in "${archs[@]}"; do
          grep "linux_${arch}" kustomize-data.raw | jq -n --raw-input --slurp '{ "releases": [
            inputs | split("\n")[]
            | select(test("^\\w+\\s+kustomize_"))
            | match("(?<digest>\\w+)\\s+kustomize_(?<version>v[\\d.]+)_(?<os>\\w+)_(?<arch>\\w+)\\..+")
            | { version: ("\(.captures[1].string)"), digest: .captures[0].string }
          ]}' > "${DATA_DIR}/kustomize-${arch}.json"
    done
}

main() {
    mkdir -p "${DATA_DIR}"
    kustomize_fetch_data
    kustomize_save_arch_sources
}

main
