TMP_DIR := $(shell mkdir -p build/tmp && realpath build/tmp)
RENOVATE_IMG := renovate/renovate:full

VALIDATE_FILE := docker run --rm -v $(shell pwd):/repo:ro -e LOG_LEVEL=debug \
		$(RENOVATE_IMG) renovate-config-validator --strict

# When GITHUB_COM_TOKEN is provided, it is automatically redacted from logs.
TEST_FILE := docker run --rm -v $(TMP_DIR):/repo:ro -e LOG_LEVEL=debug \
	-e GITHUB_COM_TOKEN --workdir /repo $(RENOVATE_IMG) renovate --platform=local --dry-run=full

validate: # validates (strict mode) all renovate files for syntax errors.
	$(VALIDATE_FILE) /repo/.github/renovate.json
	$(VALIDATE_FILE) /repo/files/renovate.json
	$(VALIDATE_FILE) /repo/default.json

data:
	@mkdir -p data && rm data/*
	@./hack/generate-data-sources.sh
	
test: data # this is to enable manual tests, not for CI.
	@rm -rf $(TMP_DIR) && mkdir -p tests && cp -r tests $(TMP_DIR)
	@mkdir -p $(TMP_DIR)/.github && cp default.json $(TMP_DIR)/.github/renovate.json
	@cp -r data $(TMP_DIR)
	$(TEST_FILE)
