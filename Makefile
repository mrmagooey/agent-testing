.PHONY: minikube-start minikube-stop minikube-delete minikube-dashboard \
        docker-build-worker docker-build-coordinator docker-build-all \
        helm-lint helm-template helm-install-minikube helm-upgrade-minikube helm-uninstall \
        redeploy redeploy-clean \
        dev-coordinator dev-frontend \
        test lint format sync \
        kind-e2e-up kind-e2e-down kind-e2e-pytest kind-e2e-playwright \
        kind-e2e-playwright-all

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

# ---------------------------------------------------------------------------
# Kind live-e2e
#
# Requires kind and OPENROUTER_TEST_KEY to be set.
# Typical workflow:
#   make kind-e2e-up                    # build images, deploy to kind
#   ./scripts/e2e-live/port-forward.sh &  # forward coordinator port
#   make kind-e2e-pytest                # run backend live tests
#   make kind-e2e-playwright            # run frontend live tests
#   make kind-e2e-down                  # tear down cluster
#
# Or for a one-shot playwright run that manages the port-forward itself:
#   make kind-e2e-playwright-all        # bootstrap + port-forward + playwright
# (leaves the cluster running so you can re-run; tear down with kind-e2e-down)
# ---------------------------------------------------------------------------

KIND_CLUSTER   ?= sec-review-e2e
KIND_RELEASE   ?= sec-review-e2e
KIND_NAMESPACE ?= sec-review-e2e
KIND_CONTEXT   ?= kind-$(KIND_CLUSTER)

kind-e2e-up:
	bash scripts/e2e-live/bootstrap.sh

kind-e2e-down:
	bash scripts/e2e-live/teardown.sh

kind-e2e-pytest:
	uv run pytest tests/e2e/live/ -m k8s_live -v

kind-e2e-playwright:
	cd frontend && E2E_LIVE=1 E2E_LIVE_BASE_URL=http://localhost:8080 npx playwright test --grep @live

kind-e2e-playwright-all:
	@set -e; \
	if ! kind get clusters 2>/dev/null | grep -qx "$(KIND_CLUSTER)"; then \
	  echo ">>> kind cluster '$(KIND_CLUSTER)' not found — bootstrapping"; \
	  bash scripts/e2e-live/bootstrap.sh; \
	else \
	  echo ">>> kind cluster '$(KIND_CLUSTER)' already up — reusing"; \
	fi; \
	echo ">>> starting port-forward (context: $(KIND_CONTEXT))"; \
	kubectl --context $(KIND_CONTEXT) -n $(KIND_NAMESPACE) port-forward svc/$(KIND_RELEASE)-coordinator 8080:8080 >/tmp/kind-e2e-pf.log 2>&1 & \
	PF_PID=$$!; \
	trap "kill $$PF_PID 2>/dev/null || true" EXIT; \
	echo ">>> waiting for port-forward to become ready"; \
	for i in $$(seq 1 30); do \
	  if curl -sf http://localhost:8080/health >/dev/null 2>&1; then \
	    echo ">>> port-forward ready"; break; \
	  fi; \
	  sleep 1; \
	  if [ $$i -eq 30 ]; then echo "ERROR: port-forward did not become ready" >&2; cat /tmp/kind-e2e-pf.log >&2; exit 1; fi; \
	done; \
	cd frontend && E2E_LIVE=1 E2E_LIVE_BASE_URL=http://localhost:8080 npx playwright test --grep @live
