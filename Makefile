BASE_DIR := $(shell pwd)

VALIDATE_FILE := docker run --rm -v $(BASE_DIR):/repo:ro -e LOG_LEVEL=debug \
		renovate/renovate renovate-config-validator

validate:
	$(VALIDATE_FILE) /repo/.github/renovate.json
	$(VALIDATE_FILE) /repo/files/renovate.json
	$(VALIDATE_FILE) /repo/default.json
