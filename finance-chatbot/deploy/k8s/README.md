# Kubernetes manifests (reference)

Example manifests for running finbot on EKS. The design and the reasons for
each setting are in [docs/design.md, section 10](../../docs/design.md). These
files have not been applied to a live cluster; replace every `ACCOUNT_ID`,
`REGISTRY`, `VERSION` and `REPLACE_ME` first.

| File | What it runs |
|---|---|
| `namespace.yaml` | `finbot` namespace with the restricted Pod Security standard |
| `serviceaccounts.yaml` | one identity per role (worker, api, ingest), each mapped to its own IAM role |
| `config.yaml` | non-secret settings; there are no secrets in the cluster |
| `worker.yaml` | queue workers (`python -m finbot.worker_main`), PDB, KEDA scaling on SQS depth |
| `api.yaml` | the trust boundary around `ChatService`; **the HTTP adapter is not in this repo yet** |
| `ingest-cronjob.yaml` | nightly incremental ingestion into the vector index |
| `networkpolicy.yaml` | default deny; workers take no inbound traffic; egress only to DNS and AWS endpoints |

Prerequisites outside the cluster: the DynamoDB session table, the SQS FIFO
queue and its FIFO dead-letter queue (redrive after 5 receives, visibility
timeout 150 s), the Bedrock guardrail (`infra/create_guardrail.py`), VPC
endpoints, and KEDA installed in the cluster.

```bash
docker build -t REGISTRY/finbot:VERSION .
kubectl apply -f deploy/k8s/
```
