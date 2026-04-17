.PHONY: minikube-start minikube-stop minikube-delete minikube-dashboard \
        docker-build-worker docker-build-coordinator docker-build-all \
        helm-lint helm-template helm-install-minikube helm-upgrade-minikube helm-uninstall \
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
	helm upgrade --install $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) --create-namespace \
		--values $(CHART_DIR)/values-minikube.yaml \
		--kube-context agent-testing

helm-upgrade-minikube: helm-install-minikube

helm-uninstall:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) --kube-context agent-testing

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
