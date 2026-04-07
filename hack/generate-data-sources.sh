#!/bin/bash

set -exo pipefail

DATA_DIR="data"

ARCHS=("amd64" "arm64" "s390x")

kustomize_fetch_data() {
  curl --retry 3 --retry-connrefused -L https://api.github.com/repos/kubernetes-sigs/kustomize/releases |
    jq -r '.[].assets[] | select(.name == "checksums.txt") | .browser_download_url' |
    head -n3 | xargs -I{} curl --retry 3 --retry-connrefused -L {} >>"${DATA_DIR}/kustomize-data.raw"
}

kubectl_fetch_data() {
  # Return the last 4 patch releases across the supported minors.
  versions=$(curl --retry 3 --retry-connrefused -L https://api.github.com/repos/kubernetes/kubernetes/releases | jq -r '.[].tag_name' | grep -v alpha | grep -v rc | grep -v beta | head -n 4)

  echo "${versions}" | while IFS= read -r version; do
    for arch in "${ARCHS[@]}"; do
      curl --retry 3 --retry-connrefused -L "https://dl.k8s.io/release/${version}/bin/linux/${arch}/kubectl.sha256" >>"${DATA_DIR}/kubectl-data.raw"
      echo "  kubectl_${version}_linux_${arch}.tar.gz" >>"${DATA_DIR}/kubectl-data.raw"
    done
  done
}

goreleaser_fetch_data() {
  # Return the last 4 patch releases across the supported minors, along with their release dates separated by a tab.
  versions=$(curl --silent --retry 3 --retry-connrefused -L https://api.github.com/repos/goreleaser/goreleaser/releases | jq -r 'map(select(.tag_name | test("alpha|rc|beta|nightly") | not)) | .[0:4][] | "\(.tag_name)\t\(.created_at)"')

  ARCH="x86_64"

  VERSION_JSON="{ \"releases\": ["
  while IFS=$'\t' read -r version created_at; do
    CHECKSUM_DATA=$(curl --retry 3 --retry-connrefused -L "https://github.com/goreleaser/goreleaser/releases/download/${version}/checksums.txt")
    checksum=$(grep "goreleaser_Linux_${ARCH}.tar.gz" <<<"$CHECKSUM_DATA" | grep -v sbom | cut -d ' ' -f1)
    VERSION_JSON+="{\"version\": \"${version}\", \"digest\": \"${checksum}\", \"releaseTimestamp\": \"${created_at}\", \"changelogUrl\": \"https://github.com/goreleaser/goreleaser/releases/tag/${version}\"},"
  done <<<"${versions}"
  # remove last comma
  VERSION_JSON="${VERSION_JSON%,}"
  VERSION_JSON+="]}"
  echo "${VERSION_JSON}" >"${DATA_DIR}/goreleaser-${ARCH}.json"

}

kustomize_save_arch_sources() {
  for arch in "${ARCHS[@]}"; do
    grep "linux_${arch}" "${DATA_DIR}/kustomize-data.raw" | jq -n --raw-input --slurp '{ "releases": [
            inputs | split("\n")[]
            | select(test("^\\w+\\s+kustomize_"))
            | match("(?<digest>\\w+)\\s+kustomize_(?<version>v[\\d.]+)_(?<os>\\w+)_(?<arch>\\w+)\\..+")
            | { version: ("\(.captures[1].string)"), digest: .captures[0].string }
          ]}' >"${DATA_DIR}/kustomize-${arch}.json"
  done
}

kubectl_save_arch_sources() {
  for arch in "${ARCHS[@]}"; do
    grep "linux_${arch}" "${DATA_DIR}/kubectl-data.raw" | jq -n --raw-input --slurp '{ "releases": [
            inputs | split("\n")[]
            | select(test("^\\w+\\s+kubectl_"))
            | match("(?<digest>\\w+)\\s+kubectl_(?<version>v[\\d.]+)_(?<os>\\w+)_(?<arch>\\w+)\\..+")
            | { version: ("\(.captures[1].string)"), digest: .captures[0].string }
          ]}' >"${DATA_DIR}/kubectl-${arch}.json"
  done
}

main() {
  mkdir -p "${DATA_DIR}"

  # Only fetch custom data in projects where they are used.
  # For projects where "# renovate-local" is not applied, skip this
  # process altogether.
  if grep -r -q --exclude-dir=renovate-config "# renovate-local:" ./; then
    kustomize_fetch_data
    kustomize_save_arch_sources
    kubectl_fetch_data
    kubectl_save_arch_sources
  fi
  if grep -r -q "GORELEASER_VERSION" ./.github/workflows; then
    goreleaser_fetch_data
  fi
}

main
