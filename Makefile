TMP_DIR := $(realpath build/tmp)
RENOVATE_IMG := renovate/renovate:slim

VALIDATE_FILE := docker run --rm -v $(shell pwd):/repo:ro -e LOG_LEVEL=debug \
		$(RENOVATE_IMG) renovate-config-validator --strict
TEST_FILE := docker run --rm -v $(TMP_DIR):/repo:ro -e LOG_LEVEL=debug \
		--workdir /repo $(RENOVATE_IMG) renovate --platform=local

validate:
	$(VALIDATE_FILE) /repo/.github/renovate.json
	$(VALIDATE_FILE) /repo/files/renovate.json
	$(VALIDATE_FILE) /repo/default.json

test:
	@rm -rf $(TMP_DIR) && cp -r tests $(TMP_DIR) && \
		mkdir -p $(TMP_DIR)/.github && cp default.json $(TMP_DIR)/.github/renovate.json
	$(TEST_FILE)
