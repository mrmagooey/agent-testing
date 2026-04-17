.PHONY: minikube-start minikube-stop minikube-delete minikube-dashboard \
        docker-build-worker docker-build-coordinator docker-build-all \
        helm-lint helm-template helm-install-minikube helm-upgrade-minikube helm-uninstall \
        redeploy redeploy-clean \
        dev-coordinator dev-frontend \
        test lint format sync

# Chart release name + release namespace — override on the command line:
#   make helm-install-minikube RELEASE=my-release NAMESPACE=my-ns
RELEASE   ?= sec-review
NAMESPACE ?= sec-review
CHART_DIR := ./helm/sec-review

# Minikube VM sizing — override on the command line if needed:
#   make minikube-start MINIKUBE_MEMORY=8192
MINIKUBE_CPUS   ?= 4
MINIKUBE_MEMORY ?= 6144

# ---------------------------------------------------------------------------
# Minikube
# ---------------------------------------------------------------------------

minikube-start:
	minikube start -p agent-testing --cpus=$(MINIKUBE_CPUS) --memory=$(MINIKUBE_MEMORY)

minikube-stop:
	minikube stop -p agent-testing

minikube-delete:
	minikube delete -p agent-testing

minikube-dashboard:
	minikube dashboard -p agent-testing

# ---------------------------------------------------------------------------
# Docker builds (inside minikube's Docker daemon)
# ---------------------------------------------------------------------------

docker-build-worker:
	eval $$(minikube -p agent-testing docker-env) && docker build -f Dockerfile.worker -t sec-review-worker:latest .

docker-build-coordinator:
	eval $$(minikube -p agent-testing docker-env) && docker build -f Dockerfile.coordinator -t sec-review-coordinator:latest .

docker-build-all: docker-build-worker docker-build-coordinator

# ---------------------------------------------------------------------------
# Helm chart
# ---------------------------------------------------------------------------

helm-lint:
	helm lint $(CHART_DIR)

helm-template:
	helm template $(RELEASE) $(CHART_DIR) --namespace $(NAMESPACE) --values $(CHART_DIR)/values-minikube.yaml

helm-install-minikube:
	DIGEST=$$(eval $$(minikube -p agent-testing docker-env) && docker image inspect sec-review-coordinator:latest --format '{{.Id}}' 2>/dev/null || true); \
	helm upgrade --install $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) --create-namespace \
		--values $(CHART_DIR)/values-minikube.yaml \
		--set-string coordinator.imageDigest=$$DIGEST \
		--kube-context agent-testing

helm-upgrade-minikube: helm-install-minikube

helm-uninstall:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) --kube-context agent-testing

# ---------------------------------------------------------------------------
# Incremental redeploy
#
# `make redeploy` rebuilds only the images whose sources changed since the
# last successful build, then runs `helm upgrade` only if any image or chart
# file changed. State lives in $(STAMP_DIR)/ (gitignored). Use
# `make redeploy-clean` to force a full rebuild on the next invocation.
# ---------------------------------------------------------------------------

STAMP_DIR := .build-stamps

COORDINATOR_SOURCES := Dockerfile.coordinator pyproject.toml \
    $(shell find src -type f -not -name '*.pyc' -not -path '*/__pycache__/*' 2>/dev/null) \
    $(shell find frontend -type f \
        -not -path 'frontend/node_modules/*' \
        -not -path 'frontend/dist/*' \
        -not -path 'frontend/playwright-report/*' \
        -not -path 'frontend/test-results/*' \
        2>/dev/null)

WORKER_SOURCES := Dockerfile.worker pyproject.toml \
    $(shell find src -type f -not -name '*.pyc' -not -path '*/__pycache__/*' 2>/dev/null)

CHART_SOURCES := $(shell find helm -type f 2>/dev/null)

$(STAMP_DIR)/coordinator: $(COORDINATOR_SOURCES)
	@mkdir -p $(STAMP_DIR)
	@echo ">>> coordinator sources changed — rebuilding image"
	eval $$(minikube -p agent-testing docker-env) && docker build -f Dockerfile.coordinator -t sec-review-coordinator:latest .
	@touch $@

$(STAMP_DIR)/worker: $(WORKER_SOURCES)
	@mkdir -p $(STAMP_DIR)
	@echo ">>> worker sources changed — rebuilding image"
	eval $$(minikube -p agent-testing docker-env) && docker build -f Dockerfile.worker -t sec-review-worker:latest .
	@touch $@

$(STAMP_DIR)/deploy: $(STAMP_DIR)/coordinator $(STAMP_DIR)/worker $(CHART_SOURCES)
	@mkdir -p $(STAMP_DIR)
	@echo ">>> images or chart changed — running helm upgrade"
	$(MAKE) helm-install-minikube
	@touch $@

redeploy: $(STAMP_DIR)/deploy
	@echo "redeploy: up to date"

redeploy-clean:
	rm -rf $(STAMP_DIR)

# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

dev-coordinator:
	uvicorn sec_review_framework.coordinator:app --reload --port 8080

dev-frontend:
	cd frontend && npm run dev

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

test:
	uv run pytest tests/ -x

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

sync:
	uv sync --all-extras
