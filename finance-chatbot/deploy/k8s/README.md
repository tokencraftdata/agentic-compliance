# Kubernetes manifests (reference)

Example manifests for running finbot on EKS. The design and the reasons for
each setting are in [docs/design.md, section 11](../../docs/design.md). These
files have not been applied to a live cluster; replace every `ACCOUNT_ID`,
`REGISTRY`, `VERSION` and `REPLACE_ME` first.

| File | What it runs |
|---|---|
| `namespace.yaml` | `finbot` namespace with the restricted Pod Security standard |
| `serviceaccounts.yaml` | one identity per role (worker, api, ingest), each mapped to its own IAM role |
| `config.yaml` | non-secret settings; there are no secrets in the cluster |
| `worker.yaml` | queue workers (`python -m finbot.worker_main`), PDB, KEDA scaling on SQS depth |
| `api.yaml` | the trust boundary: `finbot/http_api.py` under gunicorn |
| `targetgroupbinding.yaml` | registers API pods with the ALB target group from `infra/cdk` (WAF and TLS are there) |
| `ingest-cronjob.yaml` | nightly incremental ingestion into the vector index |
| `networkpolicy.yaml` | default deny; workers take no inbound traffic; egress only to DNS and AWS endpoints |

Prerequisites outside the cluster: the AWS stack in `infra/cdk` (ALB, WAF, KMS
keys, DynamoDB session table, SQS FIFO queue and dead-letter queue, write-once
audit bucket, VPC endpoints), the Bedrock guardrail (`infra/create_guardrail.py`),
the AWS Load Balancer Controller and KEDA installed in the cluster.

```bash
docker build -t REGISTRY/finbot:VERSION .
kubectl apply -f deploy/k8s/
```
