# finbot worker image. Same image for the queue worker and the ingestion job;
# the command picks the role (see deploy/k8s/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY finbot/ finbot/
COPY scripts/ scripts/
COPY data/ data/

# No root, no shell needed at runtime. AWS credentials come from the pod's
# service account (IRSA / EKS Pod Identity), never from the image or env.
RUN useradd --uid 10001 --no-create-home finbot
USER 10001

CMD ["python", "-m", "finbot.worker_main"]
