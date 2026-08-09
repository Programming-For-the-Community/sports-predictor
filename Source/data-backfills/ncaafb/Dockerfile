# Build context is Source/ (not this directory) -- the image needs both
# this job's code and the sibling library/ package, and Docker can't COPY
# anything from outside its build context. See .github/workflows/ncaafb_backfill.yml
# for the `docker build -f Source/data-backfills/ncaafb/Dockerfile Source` invocation.
#
# Multi-stage: dependencies are resolved and installed in `builder`, then
# only the installed packages plus the two application files are copied
# into `runtime` -- pip/setuptools/wheel, build caches, and pytest (dev-only,
# see requirements-dev.txt) never end up in the final image. Every
# dependency here (boto3, requests, and their transitive deps) is pure
# Python with no C extensions, so no compiler is needed in either stage.
# Mirrors data-backfills/nfl/Dockerfile exactly.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY data-backfills/ncaafb/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --target=/install ./library && \
    pip install --no-cache-dir --target=/install -r requirements.txt

# --- Runtime: installed packages + application source, nothing else ---
FROM python:3.12-slim AS runtime

RUN useradd --no-create-home --uid 10001 backfill

WORKDIR /app

ENV PYTHONPATH=/app/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /install /app/deps
COPY data-backfills/ncaafb/backfill.py data-backfills/ncaafb/normalize.py ./

USER backfill

ENTRYPOINT ["python", "backfill.py"]
