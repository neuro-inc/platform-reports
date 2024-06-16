.PHONY: all test clean
all test clean:

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
	python -m mypy platform_reports tests

test_unit:
	. venv/bin/activate; \
	pytest -vv --log-level=INFO --cov=platform_reports --cov-report xml:.coverage.unit.xml tests/unit

test_integration:
	. venv/bin/activate; \
	pytest -vv --log-level=INFO --cov=platform_reports --cov-report xml:.coverage.integration.xml tests/integration


docker_build:
	rm -rf build dist
	. venv/bin/activate; \
	pip install -U build; \
	python -m build
	docker build \
		--build-arg PYTHON_BASE=slim-buster \
		-t platform-reports:latest .
