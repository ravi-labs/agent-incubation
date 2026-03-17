# ─────────────────────────────────────────────────────────────────────────────
# Agent Foundry — Dockerfile for containerised agent deployment
#
# Build:
#   docker build -t my-agent:latest \
#     --build-arg AGENT_MODULE=my_agents.fiduciary_watchdog \
#     --build-arg AGENT_CLASS=FiduciaryWatchdogAgent \
#     .
#
# Run locally:
#   docker run \
#     -e FOUNDRY_ENV=sandbox \
#     -e FOUNDRY_MANIFEST_PATH=/app/manifest.yaml \
#     -e FOUNDRY_POLICY_DIR=/app/policies/ \
#     my-agent:latest
#
# Deploy to ECS Fargate: see ecs-task-def.json in this directory.
# Deploy to Lambda container: change ENTRYPOINT to use lambda_handler.
# ─────────────────────────────────────────────────────────────────────────────

# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps
RUN pip install --no-cache-dir hatchling

# Copy only what's needed to build the wheel
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Build the wheel
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security (important for regulated environments)
RUN groupadd --gid 1001 foundry && \
    useradd --uid 1001 --gid foundry --shell /bin/bash --create-home foundry

WORKDIR /app

# Install the pre-built wheel and runtime extras
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir \
      /wheels/agent_foundry-*.whl \
      "agent-foundry[aws]" \
      --find-links /wheels && \
    rm -rf /wheels

# Copy agent artifacts (manifest, policies, agent code)
# In a real image build, your team repo's CI copies these in.
# Pattern: COPY agents/my-agent/ /app/
COPY . /app/

# ── Environment ───────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FOUNDRY_ENV=sandbox \
    FOUNDRY_MANIFEST_PATH=/app/manifest.yaml \
    FOUNDRY_POLICY_DIR=/app/policies/ \
    FOUNDRY_LOG_LEVEL=INFO

# Build args — set the agent module and class to run
ARG AGENT_MODULE=my_agents.agent
ARG AGENT_CLASS=MyAgent
ENV AGENT_MODULE=${AGENT_MODULE} \
    AGENT_CLASS=${AGENT_CLASS}

# Drop to non-root
USER foundry

# Healthcheck — foundry CLI validates the manifest on startup
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD foundry agent validate ${FOUNDRY_MANIFEST_PATH} || exit 1

# Default: run the agent via the foundry entrypoint
# Override CMD in ECS task definition for specific trigger modes.
CMD ["python", "-c", \
     "import importlib, asyncio, os; \
      mod = importlib.import_module(os.environ['AGENT_MODULE']); \
      cls = getattr(mod, os.environ['AGENT_CLASS']); \
      from foundry.deploy.lambda_handler import make_handler; \
      print('Agent container ready:', cls.__name__)"]
