# sec-review Helm Chart

This Helm chart deploys the AI/LLM Security Code Review Testing Framework to Kubernetes.

## Quick Start

```bash
helm upgrade --install sec-review ./helm/sec-review \
  --namespace sec-review --create-namespace \
  --values ./helm/sec-review/values.yaml
```

## Configuration

See `values.yaml` for all available options. Key sections:

### Coordinator

- `coordinator.replicas` — number of API/frontend replicas (default: 1)
- `coordinator.service.port` — port to expose (default: 8080)
- `coordinator.storageRoot` — where to read/write run artifacts (default: /data)
- `coordinator.configMapName` — name of the ConfigMap containing `config/*.yaml`; automatically populated from the repo's `config/` directory via `helm/sec-review/files -> ../../config` symlink at chart-render time

### Storage

- `storage.size` — PVC size for shared artifacts (default: 100Gi)
- `storage.accessMode` — must match your K8s cluster's storage class (default: ReadWriteOnce)

### Worker Tools

Optional tool extensions are configured under `workerTools`:

```yaml
workerTools:
  treeSitter:
    enabled: true
    grammars:
      - python
      - javascript
      - go
      # ... more languages
  
  lsp:
    enabled: true
    servers:
      - pyright-langserver
      - gopls
      - typescript-language-server
      - rust-analyzer
      - clangd
  
  devdocs:
    enabled: true
    docsetsPath: /data/devdocs
    docsets:
      - python~3.12
      - javascript
      - go
      # ... more docsets
```

When tool extensions are enabled, worker pods include the necessary binaries and libraries. The devdocs extension requires a `devdocs-sync` job to run once before workers start; it populates `/data/devdocs` on the shared PVC with offline documentation from [DevDocs.io](https://devdocs.io).

The coordinator exposes a `GET /api/tool-extensions` endpoint listing which extensions are available and their capabilities.

### Ingress

- `ingress.enabled` — expose the service outside the cluster (default: false)
- `ingress.className` — ingress class to use (default: nginx)
- `ingress.hosts` — list of hostnames

### API Keys & Secrets

The coordinator requires LLM API keys. Supply them via a Kubernetes Secret:

```bash
kubectl create secret generic llm-api-keys \
  --from-literal=openrouter=$OPENROUTER_KEY \
  -n sec-review
```

Or use a tool like [external-secrets](https://external-secrets.io) for production.

## Files

- `Chart.yaml` — Helm metadata
- `values.yaml` — default configuration (production-oriented)
- `values-minikube.yaml` — local development (smaller resources, local image pull)
- `values-prod.yaml` — production template (adjust for your environment)
- `templates/` — Kubernetes resource templates
  - `coordinator-deployment.yaml` — FastAPI + React frontend
  - `worker-rbac.yaml` — ServiceAccount and Roles for worker pods
  - `storage-pvc.yaml` — shared PVC for run artifacts
  - `devdocs-sync-job.yaml` — one-shot job to download documentation

## Deployment Examples

### Local Development (Minikube)

```bash
make minikube-start
make docker-build-all
make helm-install-minikube
minikube service sec-review-coordinator -n sec-review
```

### Production (EKS / GKE / AKS)

1. Adjust `values-prod.yaml` for your cluster:
   - Update storage class (e.g., `gp2` for EKS, `standard` for GKE)
   - Set ingress class and hostname
   - Configure resource requests/limits for your cluster

2. Deploy:
   ```bash
   helm upgrade --install sec-review ./helm/sec-review \
     --namespace sec-review --create-namespace \
     --values ./helm/sec-review/values-prod.yaml \
     --set-string secrets.apiKeys.openrouter=$OPENROUTER_KEY
   ```

## Maintenance

### Updating Documentation Offline (DevDocs)

If you've added docsets to `workerTools.devdocs.docsets`, re-run the devdocs-sync job:

```bash
kubectl delete pod -l job-name=sec-review-devdocs-sync -n sec-review
kubectl wait --for=condition=ready pod -l job-name=sec-review-devdocs-sync -n sec-review
```

Or delete the job and let Helm redeploy it on the next `helm upgrade`.

## License

Internal research project.
