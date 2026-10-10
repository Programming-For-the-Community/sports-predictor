# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package. Built by
# .github/workflows/ai_training.yml (called from nhl_deploy.yml and
# app_deploy.yml) with
# `docker build -f Source/model-training/nhl/Dockerfile Source`.
#
# One image, three entrypoint scripts (train_win_probability_model.py,
# train_score_model.py, train_player_prop_model.py); CMD picks
# train_win_probability_model.py by default, the other two task
# definitions override the container command (see
# Terraform/ecs-task-nhl-train-score-model.tf and
# Terraform/ecs-task-nhl-train-player-prop-model.tf). Every script a task
# definition names must be in the COPY list below.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/nhl/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --only-binary :all: --target=/install ./library && \
    pip install --no-cache-dir --only-binary :all: --require-hashes \
        --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

# lightgbm's prebuilt wheel dynamically links libgomp.so.1 (OpenMP) at
# import time, which python:3.12-slim doesn't ship.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --no-create-home --uid 10001 train-model

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY model-training/nhl/train_win_probability_model.py \
     model-training/nhl/train_score_model.py model-training/nhl/train_player_prop_model.py \
     model-training/nhl/nhl_training.py ./

USER train-model

ENTRYPOINT ["python"]
CMD ["train_win_probability_model.py"]
