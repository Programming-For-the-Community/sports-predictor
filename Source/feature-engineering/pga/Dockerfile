# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See .github/workflows/
# pga_ai_training.yml (not yet built as of this writing -- see
# project-pga-onboarding memory) for the `docker build -f
# Source/feature-engineering/pga/Dockerfile Source` invocation, same
# convention as nba_ai_training.yml/ncaafb_ai_training.yml.
#
# Multi-stage, identical shape to Source/feature-engineering/nba/Dockerfile
# -- see that file's own comment.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY feature-engineering/pga/requirements.txt ./requirements.txt

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
COPY feature-engineering/pga/build_dataset.py ./

USER feature-engineering

ENTRYPOINT ["python", "build_dataset.py"]
