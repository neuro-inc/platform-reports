.PHONY: all test clean
all test clean:

IMAGE_NAME = platform-reports

.PHONY: venv
venv:
	poetry lock
	poetry install --with dev;

.PHONY: build
build: venv poetry-plugins

.PHONY: poetry-plugins
poetry-plugins:
	poetry self add "poetry-dynamic-versioning[plugin]"; \
    poetry self add "poetry-plugin-export";

.PHONY: setup
setup: venv
	poetry run pre-commit install;

.PHONY: lint
lint: format
	poetry run mypy --show-error-codes src tests

.PHONY: format
format:
ifdef CI
	poetry run pre-commit run --all-files --show-diff-on-failure
else
	poetry run pre-commit run --all-files
endif

.PHONY: test-unit
test-unit:
	poetry run pytest -vv --log-level=INFO --cov-config=pyproject.toml --cov=platform_reports --cov-report xml:.coverage.unit.xml tests/unit

.PHONY: test-integration
test-integration:
	poetry run pytest -vv --log-level=INFO --cov-config=pyproject.toml --cov=platform_reports --cov-report xml:.coverage.integration.xml tests/integration

.PHONY: clean-dist
clean-dist:
	rm -rf dist

.PHONY: build/image
build/image: .python-version dist
	docker build \
		--build-arg PY_VERSION=$$(cat .python-version) \
		-t $(IMAGE_NAME):latest .
	mkdir -p build
	docker image inspect $(IMAGE_NAME):latest -f '{{ .ID }}' > $@

.python-version:
	@echo "Error: .python-version file is missing!" && exit 1

.PHONY: dist
dist: build
	rm -rf build dist; \
	poetry export -f requirements.txt --without-hashes -o requirements.txt; \
	poetry build -f wheel;
