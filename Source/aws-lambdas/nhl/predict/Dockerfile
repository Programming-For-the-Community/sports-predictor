# Build context is Source/ (not this directory) -- the image needs both
# this Lambda's code and the sibling library/ package. Built for
# linux/arm64 by .github/workflows/docker_image_deploy.yml (called from
# nhl_deploy.yml and app_deploy.yml).
#
# xgboost installs --no-deps from requirements-nodeps.txt to skip its GPU
# dependency; libgomp is lightgbm's OpenMP runtime. Every module the
# handler imports from this directory must be in the COPY list below.

FROM public.ecr.aws/lambda/python:3.12 AS builder

WORKDIR /build

COPY library/ ./library/
COPY aws-lambdas/nhl/predict/requirements.txt ./requirements.txt
COPY aws-lambdas/nhl/predict/requirements-nodeps.txt ./requirements-nodeps.txt

RUN pip install --no-cache-dir --only-binary :all: --target=/install --no-deps ./library && \
    pip install --no-cache-dir --only-binary :all: --require-hashes \
        --target=/install --no-deps -r requirements-nodeps.txt && \
    pip install --no-cache-dir --only-binary :all: --require-hashes \
        --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM public.ecr.aws/lambda/python:3.12 AS runtime

# lightgbm's prebuilt wheel dynamically links libgomp.so.1 (OpenMP) at
# import time -- python base images don't ship it. NBA's own CANDIDATES
# lists include LightGBM (see library/ml/model_types.py), so whichever
# target promoted it needs this at inference time too, not just training
# (see Source/model-training/nba/Dockerfile's identical fix, confirmed
# live 2026-08-15).
RUN dnf install -y libgomp && dnf clean all

COPY --from=builder /install ${LAMBDA_TASK_ROOT}
COPY aws-lambdas/nhl/predict/handler.py aws-lambdas/nhl/predict/live_features.py \
     aws-lambdas/nhl/predict/event_prediction.py \
     aws-lambdas/nhl/predict/season_simulation.py aws-lambdas/nhl/predict/season_projection.py \
     ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
