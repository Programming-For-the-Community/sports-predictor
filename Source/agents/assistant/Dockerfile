# Build context is Source/ (not this directory) -- the image needs both
# the agent and the sibling library/ package. Built for linux/arm64, which
# AgentCore Runtime requires, by .github/workflows/docker_image_deploy.yml
# (called from agents_deploy.yml and app_deploy.yml). One image serves
# both runtimes; ASSISTANT_PROFILE picks the agent.
#
# requirements.txt is compiled with hashes from requirements.in by
# docker_build.yml before this build runs.

FROM public.ecr.aws/docker/library/python:3.12-slim AS builder

WORKDIR /build

COPY library/ ./library/
COPY agents/assistant/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --only-binary :all: --require-hashes --target=/install -r requirements.txt && \
    pip install --no-cache-dir --target=/install --no-deps ./library

# --- Runtime: installed packages + the agent, nothing else ---
FROM public.ecr.aws/docker/library/python:3.12-slim AS runtime

RUN useradd --create-home --uid 1000 agent
WORKDIR /app

COPY --from=builder /install /app/packages
COPY agents/assistant/agent.py /app/

ENV PYTHONPATH=/app/packages \
    PATH=/app/packages/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator \
    AGENT_OBSERVABILITY_ENABLED=true

USER agent
EXPOSE 8080

CMD ["opentelemetry-instrument", "python", "agent.py"]
