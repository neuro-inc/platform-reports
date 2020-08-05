MACHINE = $(shell uname -s)
CURRENT_TIME = $(shell date -u '+%Y-%m-%dT%H:%M:%SZ')

AWS_ACCOUNT_ID ?= 771188043543
AWS_REGION ?= us-east-1

TAG ?= latest

IMAGE_BASE_REPO ?= $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
IMAGE_REPO = $(IMAGE_BASE_REPO)/platform-reports
IMAGE = $(IMAGE_REPO):$(TAG)

HELM_ENV ?= dev

LINT_PATHS = platform_reports tests setup.py

WAIT_FOR_IT_URL = https://raw.githubusercontent.com/eficode/wait-for/master/wait-for
WAIT_FOR_IT = curl -s $(WAIT_FOR_IT_URL) | bash -s --

export PIP_EXTRA_INDEX_URL ?= $(shell python pip_extra_index_url.py)

setup:
	pip install -r requirements/dev.txt

format:
	isort -rc $(LINT_PATHS)
	black .

lint:
	isort -c -rc $(LINT_PATHS)
	black --check $(LINT_PATHS)
	flake8 $(LINT_PATHS)
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
	docker build --build-arg PIP_EXTRA_INDEX_URL -t $(IMAGE) .

docker_push: docker_build
ifeq ($(TAG),latest)
	$(error Docker image tag is not specified)
endif
	docker tag $(IMAGE) $(IMAGE_REPO):latest
	docker push $(IMAGE)
	docker push $(IMAGE_REPO):latest

artifactory_docker_login:
	@docker login $(ARTIFACTORY_DOCKER_REPO) \
		--username=$(ARTIFACTORY_USERNAME) \
		--password=$(ARTIFACTORY_PASSWORD)

eks_login:
	aws eks --region $(AWS_REGION) update-kubeconfig --name $(AWS_CLUSTER_NAME)

helm_install:
	curl https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get | bash -s -- -v $(HELM_VERSION)
	helm init --client-only

artifactory_helm_plugin_install:
	helm plugin install https://github.com/belitre/helm-push-artifactory-plugin

artifactory_helm_repo_add:
ifeq ($(ARTIFACTORY_USERNAME),)
	$(error Artifactory username is not specified)
endif
ifeq ($(ARTIFACTORY_PASSWORD),)
	$(error Artifactory password is not specified)
endif
	helm repo add banzaicloud-stable https://kubernetes-charts.banzaicloud.com
	@helm repo add neuro-local-public \
		$(ARTIFACTORY_HELM_REPO) \
		--username ${ARTIFACTORY_USERNAME} \
		--password ${ARTIFACTORY_PASSWORD}

_helm_expand_vars:
ifeq ($(TAG),latest)
	$(error Helm package tag is not specified)
endif
	rm -rf tmpdeploy/platform-reports
	mkdir -p tmpdeploy/platform-reports
	cp -Rf deploy/platform-reports/. tmpdeploy/platform-reports/
ifeq (,$(findstring Darwin,$(MACHINE)))
	# Linux
	sed -i "s/\$$IMAGE_REPO/$(subst /,\/,$(IMAGE_REPO))/g" tmpdeploy/platform-reports/values.yaml
	sed -i "s/\$$IMAGE_TAG/$(TAG)/g" tmpdeploy/platform-reports/values.yaml
	sed -i "s/\$$CURRENT_TIME/$(CURRENT_TIME)/g" tmpdeploy/platform-reports/values.yaml
else
	# Mac OS
	sed -i "" -e "s/\$$IMAGE_REPO/$(subst /,\/,$(IMAGE_REPO))/g" tmpdeploy/platform-reports/values.yaml
	sed -i "" -e "s/\$$IMAGE_TAG/$(TAG)/g" tmpdeploy/platform-reports/values.yaml
	sed -i "" -e "s/\$$CURRENT_TIME/$(CURRENT_TIME)/g" tmpdeploy/platform-reports/values.yaml
endif

artifactory_helm_push: _helm_expand_vars
	find tmpdeploy/platform-reports -type f -name 'values-*' -delete
	helm dependency update tmpdeploy/platform-reports
	helm package --version=$(TAG) tmpdeploy/platform-reports/
	helm push-artifactory platform-reports-$(TAG).tgz neuro-local-public
	rm platform-reports-$(TAG).tgz

helm_deploy: _helm_expand_vars
	helm dependency update tmpdeploy/platform-reports
	helm upgrade platform-reports tmpdeploy/platform-reports \
		--install \
		--wait \
		--timeout 600 \
		--namespace platform \
		-f tmpdeploy/platform-reports/values.yaml \
		-f tmpdeploy/platform-reports/values-$(HELM_ENV)-aws.yaml
