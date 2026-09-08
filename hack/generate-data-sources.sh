#!/usr/bin/env bash

set -exo pipefail

# Renovate's custom.local file:// datasource is sandboxed to the repository's
# own local checkout ("allowedDir"): absolute paths like /tmp are rejected.
# Since Renovate self-hosted runs (renovatebot/github-action) clone the target
# repository into their own directory, per-consumer-repo generated data is
# never visible to Renovate. Data files are therefore generated here and
# served to consumers over HTTPS (see default.json's
# customDatasources.local.defaultRegistryUrlTemplate) from the dedicated
# "renovate-data" branch, which .github/workflows/refresh-data.yml refreshes
# on a schedule. main/release require a reviewed PR for every push, so the
# generated data is published to that unprotected branch instead.
DATA_DIR="data"

ARCHS=("amd64" "arm64" "s390x")

kustomize_fetch_data() {
  curl --retry 3 --retry-connrefused -L https://api.github.com/repos/kubernetes-sigs/kustomize/releases?per-page=100 |
    jq -r '.[].assets[] | select(.name == "checksums.txt") | .browser_download_url' |
    head -n3 | xargs -I{} curl --retry 3 --retry-connrefused -L {} >>"${DATA_DIR}/kustomize-data.raw"
}

kubectl_fetch_data() {
  versions=$(curl --retry 3 --retry-connrefused -L https://api.github.com/repos/kubernetes/kubernetes/releases?per_page=100 |
    jq -r 'map(select(.tag_name | test("alpha|rc|beta") | not))[] | .tag_name')

  echo "${versions}" | while IFS= read -r version; do
    for arch in "${ARCHS[@]}"; do
      if ! checksum=$(curl --retry 3 --retry-connrefused --fail -L "https://dl.k8s.io/release/${version}/bin/linux/${arch}/kubectl.sha256"); then
        continue
      fi
      checksum="$(echo "${checksum}" | tr -d '\r\n')"
      if [[ ! "${checksum}" =~ ^[[:xdigit:]]{64}$ ]]; then
        continue
      fi
      echo "${checksum}  kubectl_${version}_linux_${arch}.tar.gz" >>"${DATA_DIR}/kubectl-data.raw"
    done
  done
}

helm_fetch_data() {
  versions=$(curl --silent --retry 3 --retry-connrefused -L https://api.github.com/repos/helm/helm/releases?per_page=100 |
    jq -r 'map(select(.tag_name | test("alpha|rc|beta") | not))[] | "\(.tag_name)\t\(.created_at)"')

  ARCHS=("amd64" "arm64")

  for arch in "${ARCHS[@]}"; do
    VERSION_JSON="{ \"releases\": ["

    while IFS=$'\t' read -r version created_at; do
      if ! checksum=$(curl --retry 3 --retry-connrefused --fail -L "https://get.helm.sh/helm-${version}-linux-${arch}.tar.gz.sha256sum"); then
        continue
      fi
      checksum=$(echo "${checksum}" | awk '{print $1}' | tr -d '\r\n')
      if [[ ! "${checksum}" =~ ^[[:xdigit:]]{64}$ ]]; then
        continue
      fi
      VERSION_JSON+="{\"version\": \"${version}\", \"digest\": \"${checksum}\", \"releaseTimestamp\": \"${created_at}\", \"changelogUrl\": \"https://github.com/helm/helm/releases/tag/${version}\"},"
    done <<<"${versions}"

    # remove last comma
    VERSION_JSON="${VERSION_JSON%,}"
    VERSION_JSON+="]}"
    echo "${VERSION_JSON}" >"${DATA_DIR}/helm-${arch}.json"
  done
}

yq_fetch_data() {
  versions=$(curl --silent --retry 3 --retry-connrefused -L https://api.github.com/repos/mikefarah/yq/releases?per_page=100 |
    jq -r 'map(select(.tag_name | test("alpha|rc|beta") | not))[] | "\(.tag_name)\t\(.created_at)"')

  ARCHS=("amd64" "arm64")

  # SHA-256 is the 19th field in yq's checksums file (field 1 = filename, then hashes in checksums_hashes_order)
  readonly YQ_SHA256_FIELD=19

  for arch in "${ARCHS[@]}"; do
    VERSION_JSON="{ \"releases\": ["

    while IFS=$'\t' read -r version created_at; do
      if ! CHECKSUM_DATA=$(curl --retry 3 --retry-connrefused --fail -L "https://github.com/mikefarah/yq/releases/download/${version}/checksums"); then
        continue
      fi
      checksum=$(awk "/yq_linux_${arch}\\.tar\\.gz[[:space:]]/ { print \$${YQ_SHA256_FIELD} }" <<<"${CHECKSUM_DATA}" | tr -d '\r\n')
      if [[ ! "${checksum}" =~ ^[[:xdigit:]]{64}$ ]]; then
        continue
      fi
      VERSION_JSON+="{\"version\": \"${version}\", \"digest\": \"${checksum}\", \"releaseTimestamp\": \"${created_at}\", \"changelogUrl\": \"https://github.com/mikefarah/yq/releases/tag/${version}\"},"
    done <<<"${versions}"

    # remove last comma
    VERSION_JSON="${VERSION_JSON%,}"
    VERSION_JSON+="]}"
    echo "${VERSION_JSON}" >"${DATA_DIR}/yq-${arch}.json"
  done
}

rancher_archive_fetch_data() {
  local package_name="$1"
  local repository="$2"
  local asset_name="$3"
  local versions
  versions=$(curl --silent --retry 3 --retry-connrefused -L "https://api.github.com/repos/${repository}/releases?per_page=100" |
    jq -r 'map(select(.tag_name | test("alpha|rc|beta") | not))[] | "\(.tag_name)\t\(.created_at)"')

  local version_json='{ "releases": ['
  while IFS=$'\t' read -r version created_at; do
    checksum=$(curl --retry 3 --retry-connrefused --fail -L "https://github.com/${repository}/releases/download/${version}/${asset_name}" | sha256sum | awk '{print $1}')
    version_json+="{\"version\": \"${version}\", \"digest\": \"${checksum}\", \"releaseTimestamp\": \"${created_at}\", \"changelogUrl\": \"https://github.com/${repository}/releases/tag/${version}\"},"
  done <<<"${versions}"
  version_json="${version_json%,}] }"
  echo "${version_json}" >"${DATA_DIR}/${package_name}.json"
}

rancher_checksum_fetch_data() {
  local package_name="$1"
  local repository="$2"
  local checksum_file="$3"
  local asset_name="$4"
  local versions
  versions=$(curl --silent --retry 3 --retry-connrefused -L "https://api.github.com/repos/${repository}/releases?per_page=100" |
    jq -r 'map(select(.tag_name | test("alpha|rc|beta") | not))[] | "\(.tag_name)\t\(.created_at)"')

  local version_json='{ "releases": ['
  while IFS=$'\t' read -r version created_at; do
    checksum=$(curl --retry 3 --retry-connrefused --fail -L "https://github.com/${repository}/releases/download/${version}/${checksum_file}" |
      awk -v asset_name="${asset_name}" '$0 ~ " " asset_name "$" {print $1; exit}')
    if [[ ! "${checksum}" =~ ^[[:xdigit:]]{64}$ ]]; then
      continue
    fi
    version_json+="{\"version\": \"${version}\", \"digest\": \"${checksum}\", \"releaseTimestamp\": \"${created_at}\", \"changelogUrl\": \"https://github.com/${repository}/releases/tag/${version}\"},"
  done <<<"${versions}"
  version_json="${version_json%,}] }"
  echo "${version_json}" >"${DATA_DIR}/${package_name}.json"
}

rancher_fetch_data() {
  rancher_archive_fetch_data machine-amd64 rancher/machine rancher-machine-amd64.tar.gz
  rancher_archive_fetch_data machine-arm64 rancher/machine rancher-machine-arm64.tar.gz
  rancher_checksum_fetch_data wins-exe rancher/wins sha256.txt wins.exe
  rancher_checksum_fetch_data wins-install rancher/wins sha256.txt install.ps1
  rancher_checksum_fetch_data wins-uninstall rancher/wins sha256.txt uninstall.ps1
  rancher_checksum_fetch_data system-agent-amd64 rancher/system-agent sha256sum.txt rancher-system-agent-amd64
  rancher_checksum_fetch_data system-agent-arm64 rancher/system-agent sha256sum.txt rancher-system-agent-arm64
  rancher_checksum_fetch_data system-agent-install rancher/system-agent sha256sum.txt install.sh
  rancher_checksum_fetch_data system-agent-uninstall rancher/system-agent sha256sum.txt system-agent-uninstall.sh
}

ghcli_fetch_data() {
  versions=$(curl --silent --retry 3 --retry-connrefused -L https://api.github.com/repos/cli/cli/releases?per-page=100 | jq -r 'map(select(.tag_name | test("alpha|rc|beta|nightly") | not))[] | "\(.tag_name)\t\(.created_at)"')

  ARCHS=("amd64" "arm64")

  for arch in "${ARCHS[@]}"; do
    VERSION_JSON="{ \"releases\": ["

    while IFS=$'\t' read -r version created_at; do
      CHECKSUM_DATA=$(curl --retry 3 --retry-connrefused -L "https://github.com/cli/cli/releases/download/${version}/gh_${version#v}_checksums.txt")
      checksum=$(grep "_linux_${arch}.tar.gz" <<<"$CHECKSUM_DATA" | cut -d ' ' -f1)
      VERSION_JSON+="{\"version\": \"${version}\", \"digest\": \"${checksum}\", \"releaseTimestamp\": \"${created_at}\", \"changelogUrl\": \"https://github.com/cli/cli/releases/tag/${version}\"},"
    done <<<"${versions}"

    # remove last comma
    VERSION_JSON="${VERSION_JSON%,}"
    VERSION_JSON+="]}"
    echo "${VERSION_JSON}" >"${DATA_DIR}/ghcli-${arch}.json"
  done
}

goreleaser_fetch_data() {
  versions=$(curl --silent --retry 3 --retry-connrefused -L https://api.github.com/repos/goreleaser/goreleaser/releases?per-page=100 | jq -r 'map(select(.tag_name | test("alpha|rc|beta|nightly") | not))[] | "\(.tag_name)\t\(.created_at)"')

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
            | select(. != null)
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
            | select(. != null)
            | { version: ("\(.captures[1].string)"), digest: .captures[0].string }
          ]}' >"${DATA_DIR}/kubectl-${arch}.json"
  done
}

main() {
  mkdir -p "${DATA_DIR}"
  rm -f "${DATA_DIR}/kubectl-data.raw" "${DATA_DIR}/kustomize-data.raw"

  # RENOVATE_LOCAL_DATA_FORCE_ALL=true generates every dataset unconditionally,
  # regardless of whether markers are present in the current directory tree.
  # Used by the scheduled refresh workflow in this repository, since the data
  # is now centrally hosted rather than generated per consumer repository.
  if [[ "${RENOVATE_LOCAL_DATA_FORCE_ALL:-false}" == "true" ]]; then
    kubectl_fetch_data
    kubectl_save_arch_sources
    kustomize_fetch_data
    kustomize_save_arch_sources
    goreleaser_fetch_data
    ghcli_fetch_data
    helm_fetch_data
    yq_fetch_data
    rancher_fetch_data
    return
  fi

  # Only fetch custom data in projects where they are used.
  # For kubectl, also enable this when KUBECTL_VERSION/checksum variables are present,
  # even if "# renovate-local" is not applied.
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git --exclude-dir="${DATA_DIR}" -e "# renovate-local: kubectl" -e "KUBECTL_VERSION" -e "KUBECTL_CHECKSUM_amd64" -e "KUBECTL_CHECKSUM_arm64" ./; then
    kubectl_fetch_data
    kubectl_save_arch_sources
  fi
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git --exclude-dir="${DATA_DIR}" "# renovate-local: kustomize" ./; then
    kustomize_fetch_data
    kustomize_save_arch_sources
  fi
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git --exclude-dir="${DATA_DIR}" "# renovate-local: goreleaser" ./; then
    goreleaser_fetch_data
  fi
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git --exclude-dir="${DATA_DIR}" "# renovate-local: ghcli" ./; then
    ghcli_fetch_data
  fi
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git --exclude-dir="${DATA_DIR}" -e "HELM_CHECKSUM_amd64" -e "HELM_CHECKSUM_arm64" ./; then
    helm_fetch_data
  fi
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git --exclude-dir="${DATA_DIR}" -e "YQ_CHECKSUM_amd64" -e "YQ_CHECKSUM_arm64" ./; then
    yq_fetch_data
  fi
  if grep -r -q --exclude-dir=renovate-config --exclude-dir=.git -e "CATTLE_MACHINE_CHECKSUM" -e "CATTLE_WINS_AGENT_CHECKSUM" -e "CATTLE_SYSTEM_AGENT_CHECKSUM" ./; then
    rancher_fetch_data
  fi
}

main
