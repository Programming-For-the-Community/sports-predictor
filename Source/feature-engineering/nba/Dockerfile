# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See
# .github/workflows/ai_training.yml (called from nba_deploy.yml) (step 5's own CI wiring, not yet
# built as of this writing -- see project-nba-onboarding memory) for the
# `docker build -f Source/feature-engineering/nba/Dockerfile Source`
# invocation, same convention as ai_training.yml's NFL/NCAAFB calls.
#
# Multi-stage, identical shape to Source/feature-engineering/nfl/Dockerfile
# -- see that file's own comment.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY feature-engineering/nba/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --only-binary :all: --target=/install ./library && \
    pip install --no-cache-dir --only-binary :all: --require-hashes \
        --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

RUN useradd --no-create-home --uid 10001 feature-engineering

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY feature-engineering/nba/build_dataset.py ./

USER feature-engineering

ENTRYPOINT ["python", "build_dataset.py"]
