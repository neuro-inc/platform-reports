LINT_PATHS = platform_reports tests

WAIT_FOR_IT_URL = https://raw.githubusercontent.com/eficode/wait-for/master/wait-for
WAIT_FOR_IT = curl -s $(WAIT_FOR_IT_URL) | bash -s --

YQ = docker run --rm -u root -v $(shell pwd):/workdir mikefarah/yq:4

setup:
	pip install -U pip
	pip install -e .[dev]
	pre-commit install

format:
ifdef CI
	pre-commit run --all-files --show-diff-on-failure
else
	pre-commit run --all-files
endif

lint: format
	mypy $(LINT_PATHS)

test_unit:
	pytest -vv --log-level=INFO tests/unit

test_integration:
	docker compose -f tests/integration/docker/docker-compose.yaml pull -q
	docker compose -f tests/integration/docker/docker-compose.yaml up -d
	@$(WAIT_FOR_IT) 0.0.0.0:3000 -- echo "grafana is up"
	@$(WAIT_FOR_IT) 0.0.0.0:8080 -- echo "platform-auth is up"
	@pytest -vv --log-level=INFO tests/integration; \
	exit_code=$$?; \
	docker compose -f tests/integration/docker/docker-compose.yaml down -v; \
	exit $$exit_code

docker_build:
	rm -rf build dist
	pip install -U build
	python -m build
	docker build \
		--build-arg PYTHON_BASE=slim-buster \
		-t platform-reports:latest .
