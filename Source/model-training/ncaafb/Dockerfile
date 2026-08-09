# Build context is Source/ -- see model-training/nfl/Dockerfile's own
# comment for why. Same multi-stage shape.
#
# One image, three entrypoint scripts (train_win_probability_model.py,
# train_score_model.py, train_player_prop_model.py -- same event/player
# feature datasets, same requirements.txt) -- see
# Terraform/ecs-task-ncaafb-train-score-model.tf and
# Terraform/ecs-task-ncaafb-train-player-prop-model.tf, which override the
# container command to run their own script instead of this file's
# default CMD. train_ranking_model.py is NOT part of this image -- its
# team-week feature dataset and its own training run are a distinct
# pipeline (see Dockerfile.ranking in this same directory).

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/ncaafb/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

RUN useradd --no-create-home --uid 10001 train-model

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY model-training/ncaafb/train_win_probability_model.py \
     model-training/ncaafb/train_score_model.py model-training/ncaafb/train_player_prop_model.py ./

USER train-model

ENTRYPOINT ["python"]
CMD ["train_win_probability_model.py"]
