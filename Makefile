AWS_ACCOUNT_ID ?= 771188043543
AWS_REGION ?= us-east-1

AZURE_RG_NAME ?= dev
AZURE_ACR_NAME ?= crc570d91c95c6aac0ea80afb1019a0c6f

ARTIFACTORY_DOCKER_REPO ?= neuro-docker-local-public.jfrog.io

TAG ?= latest
TAG_SLIM = $(TAG)-slim

IMAGE_NAME = platform-reports

IMAGE_BASE_REPO_gke   ?= $(GKE_DOCKER_REGISTRY)/$(GKE_PROJECT_ID)
IMAGE_BASE_REPO_aws   ?= $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
IMAGE_BASE_REPO_azure ?= $(AZURE_ACR_NAME).azurecr.io

IMAGE_REPO = ${IMAGE_BASE_REPO_${CLOUD_PROVIDER}}/${IMAGE_NAME}
IMAGE = $(IMAGE_REPO):$(TAG)
IMAGE_SLIM = $(IMAGE_REPO):$(TAG_SLIM)

ARTIFACTORY_IMAGE_REPO = $(ARTIFACTORY_DOCKER_REPO)/$(IMAGE_NAME)
ARTIFACTORY_IMAGE = $(ARTIFACTORY_IMAGE_REPO):$(TAG)
ARTIFACTORY_IMAGE_SLIM = $(ARTIFACTORY_IMAGE_REPO):$(TAG_SLIM)

HELM_ENV ?= dev
HELM_CHART = platform-reports

LINT_PATHS = platform_reports tests setup.py

WAIT_FOR_IT_URL = https://raw.githubusercontent.com/eficode/wait-for/master/wait-for
WAIT_FOR_IT = curl -s $(WAIT_FOR_IT_URL) | bash -s --

YQ = docker run --rm -v $(shell pwd):/workdir mikefarah/yq:4

PROMETHEUS_CRD_URL = https://raw.githubusercontent.com/coreos/prometheus-operator/release-0.38/example/prometheus-operator-crd

export PIP_EXTRA_INDEX_URL ?= $(shell python pip_extra_index_url.py)

setup:
	pip install -U pip
	pip install -r requirements/dev.txt
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
	python setup.py sdist
	docker build \
		--build-arg PIP_EXTRA_INDEX_URL \
		--build-arg PYTHON_BASE=buster \
		--build-arg DIST_FILENAME=`python setup.py --fullname`.tar.gz \
		-t $(IMAGE_NAME):latest .
	docker build \
		--build-arg PIP_EXTRA_INDEX_URL \
		--build-arg PYTHON_BASE=slim-buster \
		--build-arg DIST_FILENAME=`python setup.py --fullname`.tar.gz \
		-t $(IMAGE_NAME):latest-slim .

docker_push: docker_build
	docker tag $(IMAGE_NAME):latest $(IMAGE)
	docker push $(IMAGE)

	docker tag $(IMAGE_NAME):latest-slim $(IMAGE_SLIM)
	docker push $(IMAGE_SLIM)

	docker tag $(IMAGE_NAME):latest $(IMAGE_REPO):latest
	docker push $(IMAGE_REPO):latest

artifactory_docker_push: docker_build
	docker tag $(IMAGE_NAME):latest $(ARTIFACTORY_IMAGE)
	docker push $(ARTIFACTORY_IMAGE)

	docker tag $(IMAGE_NAME):latest-slim $(ARTIFACTORY_IMAGE_SLIM)
	docker push $(ARTIFACTORY_IMAGE_SLIM)

aws_k8s_login:
	aws eks --region $(AWS_REGION) update-kubeconfig --name $(CLUSTER_NAME)

azure_k8s_login:
	az aks get-credentials --resource-group $(AZURE_RG_NAME) --name $(CLUSTER_NAME)

helm_install:
	curl https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get | bash -s -- -v $(HELM_VERSION)
	helm init --client-only
	helm repo rm stable
	helm repo add stable https://charts.helm.sh/stable
	helm repo add banzaicloud https://kubernetes-charts.banzaicloud.com
	helm repo add grafana https://grafana.github.io/helm-charts
	helm plugin install https://github.com/belitre/helm-push-artifactory-plugin

_helm_fetch:
	rm -rf temp_deploy/$(HELM_CHART)
	mkdir -p temp_deploy/$(HELM_CHART)
	cp -Rf deploy/$(HELM_CHART) temp_deploy/
	find temp_deploy/$(HELM_CHART) -type f -name 'values*' -delete
	helm dependency update temp_deploy/$(HELM_CHART)
	mkdir -p temp_deploy/$(HELM_CHART)/prometheus-crds
	# CRD's in prometheus-operator helm chart are stale, fetch the latest version
	cd temp_deploy/$(HELM_CHART)/prometheus-crds; \
	curl -sLSo crd-alertmanager.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_alertmanagers.yaml; \
	curl -sLSo crd-podmonitor.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_podmonitors.yaml; \
	curl -sLSo crd-prometheus.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_prometheuses.yaml; \
	curl -sLSo crd-prometheusrules.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_prometheusrules.yaml; \
	curl -sLSo crd-servicemonitor.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_servicemonitors.yaml; \
	curl -sLSo crd-thanosrulers.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_thanosrulers.yaml
	find temp_deploy/$(HELM_CHART)/prometheus-crds -name '*.yaml' \
		| xargs -L 1 $(YQ) e -i '.metadata.annotations."helm.sh/hook" = "crd-install"'

_helm_expand_vars:
	export IMAGE_REPO=$(ARTIFACTORY_IMAGE); \
	export IMAGE_TAG=$(TAG); \
	export IMAGE_SLIM_TAG=$(TAG_SLIM); \
	export DOCKER_SERVER=$(ARTIFACTORY_DOCKER_REPO); \
	cat deploy/$(HELM_CHART)/values-template.yaml | envsubst > temp_deploy/$(HELM_CHART)/values.yaml

artifactory_helm_push: _helm_fetch _helm_expand_vars
	helm package --version=$(TAG) --app-version=$(TAG) temp_deploy/$(HELM_CHART)
	helm push-artifactory $(HELM_CHART)-$(TAG).tgz $(ARTIFACTORY_HELM_REPO) \
		--username ${ARTIFACTORY_USERNAME} \
		--password ${ARTIFACTORY_PASSWORD}
	rm $(HELM_CHART)-$(TAG).tgz

helm_deploy: _helm_fetch _helm_expand_vars
	helm upgrade $(HELM_CHART) temp_deploy/$(HELM_CHART) \
		--install \
		--wait \
		--timeout 600 \
		--namespace platform \
		-f deploy/$(HELM_CHART)/values-$(HELM_ENV)-$(CLOUD_PROVIDER).yaml \
		--set "image.repository=$(IMAGE_REPO)"
