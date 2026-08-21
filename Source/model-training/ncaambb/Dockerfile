# Build context is Source/ (not this directory) -- see
# Source/model-training/nba/Dockerfile's own comment for the full
# reasoning, identical here. .github/workflows/ncaambb_ai_training.yml
# builds this with `docker build -f Source/model-training/ncaambb/Dockerfile
# Source`, same convention as nfl_ai_training.yml/nba_ai_training.yml.
#
# One image, three entrypoint scripts (train_win_probability_model.py,
# train_score_model.py, train_player_prop_model.py) -- same reasoning as
# NBA's own Dockerfile; CMD picks train_win_probability_model.py by
# default, the other two task definitions override the container command
# (see Terraform/ecs-task-ncaambb-train-score-model.tf and
# Terraform/ecs-task-ncaambb-train-player-prop-model.tf).
# train_ranking_model.py is NOT part of this image -- its team-poll
# feature dataset and its own training run are a distinct pipeline (see
# Dockerfile.ranking in this same directory).

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/ncaambb/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

# lightgbm's prebuilt wheel dynamically links libgomp.so.1 (OpenMP) at
# import time -- python:3.12-slim doesn't ship it (confirmed live for
# NBA's own image, 2026-08-15). xgboost/scikit-learn-intelex ship their
# own OpenMP runtime bundled in the wheel, so this is a lightgbm-only
# requirement, not a general one.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --uid 10001 train-model

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY model-training/ncaambb/train_win_probability_model.py \
     model-training/ncaambb/train_score_model.py model-training/ncaambb/train_player_prop_model.py ./

USER train-model

ENTRYPOINT ["python"]
CMD ["train_win_probability_model.py"]
