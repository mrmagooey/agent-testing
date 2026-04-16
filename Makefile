.PHONY: minikube-start minikube-stop minikube-delete minikube-dashboard \
        docker-build-worker docker-build-coordinator docker-build-all \
        k8s-apply k8s-delete \
        dev-coordinator dev-frontend \
        test lint format sync

# ---------------------------------------------------------------------------
# Minikube
# ---------------------------------------------------------------------------

minikube-start:
	minikube start -p agent-testing --cpus=4 --memory=8192

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
# Kubernetes
# ---------------------------------------------------------------------------

k8s-apply:
	kubectl apply -f k8s/ --context agent-testing

k8s-delete:
	kubectl delete -f k8s/ --context agent-testing

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
	python -m pytest tests/ -x

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

sync:
	uv sync --all-extras
