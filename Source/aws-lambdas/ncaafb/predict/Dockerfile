# Build context is Source/ (not this directory) -- same reasoning as
# Source/aws-lambdas/nfl/predict/Dockerfile: this image needs both this
# function's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See
# .github/workflows/ncaafb_ai_hosting.yml for the `docker build -f
# Source/aws-lambdas/ncaafb/predict/Dockerfile Source` invocation.
#
# Container image, not the zip packaging every other Lambda in this repo
# uses -- same xgboost/numpy/scipy size reasoning as NFL's own predict
# Dockerfile (see its own comment). requirements-nodeps.txt/
# requirements.txt split for the identical reason too (dodging xgboost's
# unconditional nvidia-nccl-cu12 GPU dependency on Linux).
#
# boto3 is intentionally excluded from requirements.txt -- the Lambda
# Python base image ships it preinstalled, same as the managed (zip)
# runtime does.

FROM public.ecr.aws/lambda/python:3.12 AS builder

WORKDIR /build

COPY library/ ./library/
COPY aws-lambdas/ncaafb/predict/requirements.txt ./requirements.txt
COPY aws-lambdas/ncaafb/predict/requirements-nodeps.txt ./requirements-nodeps.txt

RUN pip install --no-cache-dir --target=/install --no-deps ./library && \
    pip install --no-cache-dir --target=/install --no-deps -r requirements-nodeps.txt && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM public.ecr.aws/lambda/python:3.12 AS runtime

COPY --from=builder /install ${LAMBDA_TASK_ROOT}
COPY aws-lambdas/ncaafb/predict/handler.py aws-lambdas/ncaafb/predict/live_features.py \
     aws-lambdas/ncaafb/predict/model_loader.py aws-lambdas/ncaafb/predict/event_prediction.py \
     aws-lambdas/ncaafb/predict/season_projection.py aws-lambdas/ncaafb/predict/season_simulation.py \
     ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
