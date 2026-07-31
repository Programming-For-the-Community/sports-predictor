# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See
# .github/workflows/nfl_ai_training.yml for the `docker build -f
# Source/feature-engineering/nfl/Dockerfile Source` invocation.
#
# Multi-stage, same shape as Source/data-backfills/nfl/Dockerfile:
# dependencies resolved and installed in `builder`, then only the
# installed packages plus the application file are copied into `runtime`.
# Unlike backfill's dependencies, pandas/pyarrow (see requirements.txt)
# aren't pure Python -- but both ship prebuilt manylinux wheels for
# standard Fargate architectures, so `pip install` still just downloads a
# wheel here; no compiler needed in either stage.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY feature-engineering/nfl/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

RUN useradd --no-create-home --uid 10001 feature-engineering

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY feature-engineering/nfl/build_dataset.py ./

USER feature-engineering

ENTRYPOINT ["python", "build_dataset.py"]
