# Live E2E Test Fixture

This fixture provides a minimal dataset for live end-to-end testing of the security review framework.

**Dataset Details:**
- Name: `live-e2e`
- Version: `1.0.0`
- Target repo: `repo/app.py` — a tiny Python file with one seeded SQL injection vulnerability
- Ground truth labels: `labels.jsonl` — one GroundTruthLabel pointing to the SQLi on line 4

**Purpose:**
Used by live e2e tests that exercise the full ExperimentWorker pipeline against cheap LLM models (e.g., Llama 3.1 8B). The fixture is seeded into the cluster's shared PV by `scripts/e2e-live/seed-dataset.sh` during test setup.

**Scaling:**
The repo is intentionally minimal so even a cheap 8B model can reliably find the single, obvious SQLi vulnerability.
