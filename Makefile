TMP_DIR := $(shell mkdir -p build/tmp && realpath build/tmp)
RENOVATE_IMG := renovate/renovate:full
RENOVATE_CONFIGS := $(shell find . .github files -maxdepth 1 -name '*.json' 2>/dev/null)

VALIDATE_FILE := docker run --rm -v $(shell pwd):/repo:ro -e LOG_LEVEL=debug \
		$(RENOVATE_IMG) renovate-config-validator --strict

# When GITHUB_COM_TOKEN is provided, it is automatically redacted from logs.
TEST_FILE := docker run --rm -v $(TMP_DIR):/repo:ro -e LOG_LEVEL=debug \
	-e GITHUB_COM_TOKEN --workdir /repo $(RENOVATE_IMG) renovate --platform=local --dry-run=full

validate: # validates (strict mode) all renovate files for syntax errors.
	@for config in $(RENOVATE_CONFIGS); do \
		echo "Validating $$config"; \
		$(VALIDATE_FILE) /repo/$$config || exit 1; \
	done

data:
	@mkdir -p data && rm data/*
	@./hack/generate-data-sources.sh
	
test: data # this is to enable manual tests, not for CI.
	@rm -rf $(TMP_DIR) && mkdir -p tests && cp -r tests $(TMP_DIR)
	@mkdir -p $(TMP_DIR)/.github && cp default.json $(TMP_DIR)/.github/renovate.json
	@cp -r data $(TMP_DIR)
	$(TEST_FILE)
