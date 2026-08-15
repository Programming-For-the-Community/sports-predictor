# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See
# .github/workflows/nba_ai_training.yml (step 5's own CI wiring, not yet
# built as of this writing -- see project-nba-onboarding memory) for the
# `docker build -f Source/feature-engineering/nba/Dockerfile Source`
# invocation, same convention as nfl_ai_training.yml/ncaafb_ai_training.yml.
#
# Multi-stage, identical shape to Source/feature-engineering/nfl/Dockerfile
# -- see that file's own comment.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY feature-engineering/nba/requirements.txt ./requirements.txt

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
COPY feature-engineering/nba/build_dataset.py ./

USER feature-engineering

ENTRYPOINT ["python", "build_dataset.py"]
