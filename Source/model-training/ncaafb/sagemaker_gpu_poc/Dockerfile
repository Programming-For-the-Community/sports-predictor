# One-off image for the SageMaker GPU training benchmark (see this
# directory's README.md) -- built and pushed manually via workflow_dispatch
# (.github/workflows/ncaafb_sagemaker_gpu_poc_build.yml), NOT part of
# ncaafb_deploy.yml or any other automated pipeline. Same build shape as
# model-training/ncaafb/Dockerfile (library/ + requirements.txt into a
# deps layer, app code copied in separately), with two differences for
# SageMaker's "bring your own container" training contract:
#
#   1. SageMaker always starts a BYOC training container as
#      `docker run image train` (see AWS docs "Use Your Own Training
#      Algorithms"). ENTRYPOINT below is the real training script itself,
#      so that trailing "train" arrives as an unused positional arg --
#      train_player_prop_model.main() reads its config from environment
#      variables, not argv, so it's a no-op.
#   2. XGBOOST_DEVICE=cuda is baked in as a default so this image always
#      trains XGBoost on GPU without depending on a console/API caller to
#      set it (library/ml/model_types.py's _XGB_GPU_DEVICE reads this).
#
# Build context is Source/ -- same as model-training/ncaafb/Dockerfile.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY model-training/ncaafb/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

RUN useradd --no-create-home --uid 10001 train-model

WORKDIR /opt/ml/code

ENV PYTHONPATH=/opt/ml/code/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XGBOOST_DEVICE=cuda

COPY --from=builder /install ./deps
COPY model-training/ncaafb/train_player_prop_model.py ./

USER train-model

ENTRYPOINT ["python", "train_player_prop_model.py"]
