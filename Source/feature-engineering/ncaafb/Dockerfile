# Build context is Source/ -- see feature-engineering/nfl/Dockerfile's
# own comment for why (build_dataset.py needs the sibling library/
# package, which Docker can't COPY from outside its build context).
# Same multi-stage shape.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY feature-engineering/ncaafb/requirements.txt ./requirements.txt

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
COPY feature-engineering/ncaafb/build_dataset.py ./

USER feature-engineering

ENTRYPOINT ["python", "build_dataset.py"]
