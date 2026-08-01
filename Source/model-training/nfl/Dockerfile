# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See
# .github/workflows/nfl_ai_training.yml for the `docker build -f
# Source/model-training/nfl/Dockerfile Source` invocation.
#
# Multi-stage, same shape as Source/feature-engineering/nfl/Dockerfile:
# dependencies resolved and installed in `builder`, then only the
# installed packages plus the application file are copied into `runtime`.
# xgboost/pandas/pyarrow/scikit-learn (see requirements.txt) aren't pure
# Python, but all ship prebuilt manylinux wheels for standard Fargate
# architectures, so `pip install` still just downloads a wheel; no
# compiler needed in either stage.
#
# One image, four entrypoint scripts (train_model.py for the production
# win-probability XGBoost model, train_baseline_model.py for its logistic
# regression comparison baseline, train_score_model.py for the
# margin/home-score/away-score models, train_player_prop_model.py for
# every player-prop stat -- see model_common.py) -- they share every
# dependency in requirements.txt, so a second image/build job per script
# would just duplicate this one. CMD picks train_model.py by default; the
# other three task definitions override the container command to run
# their own script instead (see Terraform/ecs-task-nfl-train-baseline-model.tf,
# Terraform/ecs-task-nfl-train-score-model.tf, and
# Terraform/ecs-task-nfl-train-player-prop-model.tf).

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/nfl/requirements.txt ./requirements.txt

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
COPY model-training/nfl/train_model.py model-training/nfl/train_baseline_model.py \
     model-training/nfl/train_score_model.py model-training/nfl/train_player_prop_model.py \
     model-training/nfl/model_common.py ./

USER train-model

ENTRYPOINT ["python"]
CMD ["train_model.py"]
