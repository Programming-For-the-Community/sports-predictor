# Build context is Source/ (not this directory) -- see
# Source/model-training/nfl/Dockerfile's own comment for the full
# reasoning, identical here. .github/workflows/nba_ai_training.yml (step
# 5's own CI wiring, not yet built as of this writing -- see
# project-nba-onboarding memory) builds this with `docker build -f
# Source/model-training/nba/Dockerfile Source`, same convention as
# nfl_ai_training.yml/ncaafb_ai_training.yml.
#
# One image, three entrypoint scripts (train_win_probability_model.py,
# train_score_model.py, train_player_prop_model.py) -- same reasoning as
# NFL's own Dockerfile; CMD picks train_win_probability_model.py by
# default, the other two task definitions override the container command
# (see Terraform/ecs-task-nba-train-score-model.tf and
# Terraform/ecs-task-nba-train-player-prop-model.tf).

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/nba/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

# lightgbm's prebuilt wheel dynamically links libgomp.so.1 (OpenMP) at
# import time -- python:3.12-slim doesn't ship it, so `import lightgbm`
# fails with a LoadLibrary error otherwise (confirmed live, 2026-08-15).
# xgboost/scikit-learn-intelex ship their own OpenMP runtime bundled in
# the wheel, so this is a lightgbm-only requirement, not a general one.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --uid 10001 train-model

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY model-training/nba/train_win_probability_model.py \
     model-training/nba/train_score_model.py model-training/nba/train_player_prop_model.py ./

USER train-model

ENTRYPOINT ["python"]
CMD ["train_win_probability_model.py"]
