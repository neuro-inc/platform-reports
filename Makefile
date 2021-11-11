AWS_ACCOUNT_ID ?= 771188043543
AWS_REGION ?= us-east-1

AZURE_RG_NAME ?= dev
AZURE_ACR_NAME ?= crc570d91c95c6aac0ea80afb1019a0c6f

GITHUB_OWNER ?= neuro-inc

IMAGE_TAG ?= latest

IMAGE_REPO_gke    = $(GKE_DOCKER_REGISTRY)/$(GKE_PROJECT_ID)
IMAGE_REPO_aws    = $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
IMAGE_REPO_azure  = $(AZURE_ACR_NAME).azurecr.io
IMAGE_REPO_github = ghcr.io/$(GITHUB_OWNER)

IMAGE_REGISTRY ?= aws

IMAGE_NAME      = platform-reports
IMAGE_REPO_BASE = $(IMAGE_REPO_$(IMAGE_REGISTRY))
IMAGE_REPO      = $(IMAGE_REPO_BASE)/$(IMAGE_NAME)

HELM_ENV           ?= dev
HELM_CHART          = platform-reports
HELM_CHART_VERSION ?= 1.0.0
HELM_APP_VERSION   ?= 1.0.0

LINT_PATHS = platform_reports tests

WAIT_FOR_IT_URL = https://raw.githubusercontent.com/eficode/wait-for/master/wait-for
WAIT_FOR_IT = curl -s $(WAIT_FOR_IT_URL) | bash -s --

YQ = docker run --rm -u root -v $(shell pwd):/workdir mikefarah/yq:4

PROMETHEUS_CRD_URL = https://raw.githubusercontent.com/coreos/prometheus-operator/release-0.38/example/prometheus-operator-crd

setup:
	pip install -U pip
	pip install -e .[dev]
	pre-commit install

format:
ifdef CI_LINT_RUN
	pre-commit run --all-files --show-diff-on-failure
else
	pre-commit run --all-files
endif

lint: format
	mypy $(LINT_PATHS)

test_unit:
	pytest -vv --log-level=INFO tests/unit

test_integration:
	docker-compose -f tests/integration/docker/docker-compose.yaml pull -q
	docker-compose -f tests/integration/docker/docker-compose.yaml up -d
	@$(WAIT_FOR_IT) 0.0.0.0:3000 -- echo "grafana is up"
	@$(WAIT_FOR_IT) 0.0.0.0:8080 -- echo "platform-auth is up"
	@pytest -vv --log-level=INFO tests/integration; \
	exit_code=$$?; \
	docker-compose -f tests/integration/docker/docker-compose.yaml down -v; \
	exit $$exit_code

docker_build:
	rm -rf build dist
	pip install -U build
	python -m build
	docker build \
		--build-arg PYTHON_BASE=slim-buster \
		-t $(IMAGE_NAME):latest .

docker_push: docker_build
	docker tag $(IMAGE_NAME):latest $(IMAGE_REPO):$(IMAGE_TAG)
	docker push $(IMAGE_REPO):$(IMAGE_TAG)

	docker tag $(IMAGE_NAME):latest $(IMAGE_REPO):latest
	docker push $(IMAGE_REPO):latest

aws_k8s_login:
	aws eks --region $(AWS_REGION) update-kubeconfig --name $(CLUSTER_NAME)

azure_k8s_login:
	az aks get-credentials --resource-group $(AZURE_RG_NAME) --name $(CLUSTER_NAME)

_helm_fetch_crds:
	mkdir charts/$(HELM_CHART)/crds
	# CRD's in prometheus-operator helm chart are stale, fetch the latest version
	cd charts/$(HELM_CHART)/crds; \
	curl -sLSo crd-alertmanager.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_alertmanagers.yaml; \
	curl -sLSo crd-podmonitor.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_podmonitors.yaml; \
	curl -sLSo crd-prometheus.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_prometheuses.yaml; \
	curl -sLSo crd-prometheusrules.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_prometheusrules.yaml; \
	curl -sLSo crd-servicemonitor.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_servicemonitors.yaml; \
	curl -sLSo crd-thanosrulers.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_thanosrulers.yaml

helm_create_chart: _helm_fetch_crds
	export IMAGE_REPO=$(IMAGE_REPO); \
	export IMAGE_TAG=$(IMAGE_TAG); \
	export CHART_VERSION=$(HELM_CHART_VERSION); \
	export APP_VERSION=$(HELM_APP_VERSION); \
	VALUES=$$(cat charts/$(HELM_CHART)/values.yaml | envsubst); \
	echo "$$VALUES" > charts/$(HELM_CHART)/values.yaml; \
	CHART=$$(cat charts/$(HELM_CHART)/Chart.yaml | envsubst); \
	echo "$$CHART" > charts/$(HELM_CHART)/Chart.yaml

helm_deploy: helm_create_chart
	helm dependency update charts/$(HELM_CHART)
	helm upgrade $(HELM_CHART) charts/$(HELM_CHART) \
		-f charts/$(HELM_CHART)/values-$(HELM_ENV).yaml \
		--namespace platform --install --wait --timeout 600s
