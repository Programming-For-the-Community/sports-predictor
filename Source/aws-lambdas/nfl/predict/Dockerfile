# Build context is Source/ (not this directory) -- same reasoning as
# Source/model-training/nfl/Dockerfile: this image needs both this
# function's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See
# .github/workflows/nfl_ai_hosting.yml for the `docker build -f
# Source/aws-lambdas/nfl/predict/Dockerfile Source` invocation.
#
# Container image, not the zip packaging every other Lambda in this repo
# uses (see .github/workflows/python_lambda_build.yml) -- xgboost pulls in
# numpy and scipy, which alone measure ~225MB unzipped for this runtime,
# leaving almost no headroom under Lambda's 250MB unzipped zip limit.
# Container Lambdas get a 10GB image limit instead, which comfortably
# fits with room to grow. Both builder and runtime stages use the same
# AWS-published base image so wheels built against it are guaranteed ABI
# -compatible with where they run -- no separate manylinux-tag concerns.
#
# requirements.txt installs with --no-deps (see that file's own comment)
# to skip xgboost's unconditional nvidia-nccl-cu12 dependency on Linux --
# safe because scipy/numpy are listed directly in requirements.txt, so
# --no-deps only drops what xgboost itself would otherwise pull in.
#
# boto3 is intentionally excluded from requirements.txt -- the Lambda
# Python base image ships it preinstalled, same as the managed (zip)
# runtime does.

FROM public.ecr.aws/lambda/python:3.12 AS builder

WORKDIR /build

COPY library/ ./library/
COPY aws-lambdas/nfl/predict/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install --no-deps ./library && \
    pip install --no-cache-dir --target=/install --no-deps -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM public.ecr.aws/lambda/python:3.12 AS runtime

COPY --from=builder /install ${LAMBDA_TASK_ROOT}
COPY aws-lambdas/nfl/predict/handler.py aws-lambdas/nfl/predict/live_features.py \
     aws-lambdas/nfl/predict/model_loader.py aws-lambdas/nfl/predict/season_simulation.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
