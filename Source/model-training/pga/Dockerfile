# Build context is Source/ (not this directory) -- see
# Source/model-training/nba/Dockerfile's own comment for the full
# reasoning, identical here. .github/workflows/pga_ai_training.yml builds
# this with `docker build -f Source/model-training/pga/Dockerfile
# Source`, same convention as nba_ai_training.yml.
#
# One image, five entrypoint scripts (train_top10_model.py,
# train_top5_model.py, train_score_model.py, train_cutline_model.py,
# train_round_model.py) -- same reasoning as NBA's own Dockerfile: all
# five share identical dependencies (xgboost/scikit-learn/lightgbm), so
# one image serves every PGA training target, with each target's own ECS
# task definition overriding `command` to pick its script (and, for
# train_round_model.py, its own ROUND_NUMBER environment variable --
# Terraform/ecs-task-pga-train-round-model.tf). CMD picks
# train_top10_model.py by default (the original single-script image);
# every other task definition's own `command` override picks a different
# script.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/pga/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

# lightgbm's prebuilt wheel dynamically links libgomp.so.1 (OpenMP) at
# import time -- python:3.12-slim doesn't ship it, so `import lightgbm`
# fails with a LoadLibrary error otherwise (confirmed live, 2026-08-15,
# on NBA's own training image -- see that Dockerfile's own comment).
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --uid 10001 train-model

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY model-training/pga/train_top10_model.py \
     model-training/pga/train_top5_model.py \
     model-training/pga/train_score_model.py \
     model-training/pga/train_cutline_model.py \
     model-training/pga/train_round_model.py ./

USER train-model

ENTRYPOINT ["python"]
CMD ["train_top10_model.py"]
