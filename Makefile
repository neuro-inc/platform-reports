.PHONY: all test clean
all test clean:

IMAGE_NAME = platform-reports

venv:
	python -m venv venv
	. venv/bin/activate; \
	python -m pip install --upgrade pip

.PHONY: setup
setup: venv
	. venv/bin/activate; \
	pip install -e .[dev]; \
	pre-commit install

.PHONY: lint
lint:
	. venv/bin/activate; \
	python -m pre_commit run --all-files
	. venv/bin/activate; \
	python -m mypy src tests

.PHONY: test-unit
test-unit:
	. venv/bin/activate; \
	pytest -vv --log-level=INFO --cov=platform_reports --cov-report xml:.coverage.unit.xml tests/unit

.PHONY: test-integration
test-integration:
	. venv/bin/activate; \
	pytest -vv --log-level=INFO --cov=platform_reports --cov-report xml:.coverage.integration.xml tests/integration

dist: venv setup.cfg pyproject.toml $(shell find src -type f)
	make clean-dist
	. venv/bin/activate; \
	pip install -U build; \
	python -m build --wheel ./;

.PHONY: clean-dist
clean-dist:
	rm -rf dist

build/image: .dockerignore Dockerfile dist
	docker build \
		--build-arg PY_VERSION=$$(cat .python-version) \
		-t $(IMAGE_NAME):latest .
	mkdir -p build
	docker image inspect $(IMAGE_NAME):latest -f '{{ .ID }}' > $@
