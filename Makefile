MACHINE = $(shell uname -s)
CURRENT_TIME = $(shell date -u '+%Y-%m-%dT%H:%M:%SZ')

AWS_ACCOUNT_ID ?= 771188043543
AWS_REGION ?= us-east-1

AZURE_RG_NAME ?= dev
AZURE_ACR_NAME ?= crc570d91c95c6aac0ea80afb1019a0c6f

TAG ?= latest

IMAGE_BASE_REPO_gke   ?= $(GKE_DOCKER_REGISTRY)/$(GKE_PROJECT_ID)
IMAGE_BASE_REPO_aws   ?= $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
IMAGE_BASE_REPO_azure ?= $(AZURE_ACR_NAME).azurecr.io
IMAGE_BASE_REPO  ?= ${IMAGE_BASE_REPO_${CLOUD_PROVIDER}}
IMAGE_REPO = $(IMAGE_BASE_REPO)/platform-reports
IMAGE = $(IMAGE_REPO):$(TAG)
IMAGE_SLIM = $(IMAGE_REPO):$(TAG)-slim

HELM_ENV ?= dev

LINT_PATHS = platform_reports tests setup.py

WAIT_FOR_IT_URL = https://raw.githubusercontent.com/eficode/wait-for/master/wait-for
WAIT_FOR_IT = curl -s $(WAIT_FOR_IT_URL) | bash -s --

YQ = docker run --rm -v $(shell pwd):/workdir mikefarah/yq:4

PROMETHEUS_CRD_URL = https://raw.githubusercontent.com/coreos/prometheus-operator/release-0.38/example/prometheus-operator-crd

export PIP_EXTRA_INDEX_URL ?= $(shell python pip_extra_index_url.py)

setup:
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
		-t $(IMAGE) .
	docker build \
		--build-arg PIP_EXTRA_INDEX_URL \
		--build-arg PYTHON_BASE=slim-buster \
		--build-arg DIST_FILENAME=`python setup.py --fullname`.tar.gz \
		-t $(IMAGE_SLIM) .

docker_push: docker_build
ifeq ($(TAG),latest)
	$(error Docker image tag is not specified)
endif
	docker tag $(IMAGE) $(IMAGE_REPO):latest
	docker push $(IMAGE)
	docker push $(IMAGE_SLIM)
	docker push $(IMAGE_REPO):latest

artifactory_docker_login:
	@docker login $(ARTIFACTORY_DOCKER_REPO) \
		--username=$(ARTIFACTORY_USERNAME) \
		--password=$(ARTIFACTORY_PASSWORD)

aws_k8s_login:
	aws eks --region $(AWS_REGION) update-kubeconfig --name $(CLUSTER_NAME)

azure_k8s_login:
	az aks get-credentials --resource-group $(AZURE_RG_NAME) --name $(CLUSTER_NAME)

helm_install:
	curl https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get | bash -s -- -v $(HELM_VERSION)
	helm init --client-only
	helm repo add banzaicloud https://kubernetes-charts.banzaicloud.com

artifactory_helm_plugin_install:
	helm plugin install https://github.com/belitre/helm-push-artifactory-plugin

artifactory_helm_repo_add:
ifeq ($(ARTIFACTORY_USERNAME),)
	$(error Artifactory username is not specified)
endif
ifeq ($(ARTIFACTORY_PASSWORD),)
	$(error Artifactory password is not specified)
endif
	@helm repo add neuro-local-public \
		$(ARTIFACTORY_HELM_REPO) \
		--username ${ARTIFACTORY_USERNAME} \
		--password ${ARTIFACTORY_PASSWORD}

_helm_fetch:
	rm -rf tmpdeploy/platform-reports
	mkdir -p tmpdeploy/platform-reports
	cp -Rf deploy/platform-reports/. tmpdeploy/platform-reports/
	helm dependency update tmpdeploy/platform-reports
	mkdir -p tmpdeploy/platform-reports/prometheus-crds
	# CRD's in prometheus-operator helm chart are stale, fetch the latest version
	cd tmpdeploy/platform-reports/prometheus-crds; \
	curl -sLSo crd-alertmanager.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_alertmanagers.yaml; \
	curl -sLSo crd-podmonitor.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_podmonitors.yaml; \
	curl -sLSo crd-prometheus.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_prometheuses.yaml; \
	curl -sLSo crd-prometheusrules.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_prometheusrules.yaml; \
	curl -sLSo crd-servicemonitor.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_servicemonitors.yaml; \
	curl -sLSo crd-thanosrulers.yaml $(PROMETHEUS_CRD_URL)/monitoring.coreos.com_thanosrulers.yaml
	find tmpdeploy/platform-reports/prometheus-crds -name '*.yaml' \
		| xargs -L 1 $(YQ) e -i '.metadata.annotations."helm.sh/hook" = "crd-install"'

_helm_expand_vars:
ifeq (,$(findstring Darwin,$(MACHINE)))
	# Linux
	sed -i "s/\$$IMAGE_REPO/$(subst /,\/,$(IMAGE_REPO))/g" tmpdeploy/platform-reports/values.yaml
	sed -i "s/\$$IMAGE_TAG/$(TAG)/g" tmpdeploy/platform-reports/values.yaml
	sed -i "s/\$$IMAGE_SLIM_TAG/$(TAG)-slim/g" tmpdeploy/platform-reports/values.yaml
	sed -i "s/\$$CURRENT_TIME/$(CURRENT_TIME)/g" tmpdeploy/platform-reports/values.yaml
else
	# Mac OS
	sed -i "" -e "s/\$$IMAGE_REPO/$(subst /,\/,$(IMAGE_REPO))/g" tmpdeploy/platform-reports/values.yaml
	sed -i "" -e "s/\$$IMAGE_TAG/$(TAG)/g" tmpdeploy/platform-reports/values.yaml
	sed -i "" -e "s/\$$IMAGE_SLIM_TAG/$(TAG)-slim/g" tmpdeploy/platform-reports/values.yaml
	sed -i "" -e "s/\$$CURRENT_TIME/$(CURRENT_TIME)/g" tmpdeploy/platform-reports/values.yaml
endif

artifactory_helm_push: _helm_fetch _helm_expand_vars
ifeq ($(TAG),latest)
	$(error Helm package tag is not specified)
endif
	find tmpdeploy/platform-reports -type f -name 'values-*' -delete
	helm package --version=$(TAG) tmpdeploy/platform-reports/
	helm push-artifactory platform-reports-$(TAG).tgz neuro-local-public
	rm platform-reports-$(TAG).tgz

helm_deploy: _helm_fetch _helm_expand_vars
ifeq ($(TAG),latest)
	$(error Helm package tag is not specified)
endif
	helm upgrade platform-reports tmpdeploy/platform-reports \
		--install \
		--wait \
		--timeout 600 \
		--namespace platform \
		-f tmpdeploy/platform-reports/values.yaml \
		-f tmpdeploy/platform-reports/values-$(HELM_ENV)-$(CLOUD_PROVIDER).yaml
