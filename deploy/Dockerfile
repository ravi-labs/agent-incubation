# ─────────────────────────────────────────────────────────────────────────────
# Arc Platform — Dockerfile for containerised agent deployment
#
# Build (from the repo root):
#   docker build -t my-agent:latest \
#     --build-arg AGENT_MODULE=my_agents.fiduciary_watchdog \
#     --build-arg AGENT_CLASS=FiduciaryWatchdogAgent \
#     -f deploy/Dockerfile .
#
# Run locally:
#   docker run \
#     -e ARC_ENV=sandbox \
#     -e ARC_MANIFEST_PATH=/app/manifest.yaml \
#     -e ARC_POLICY_DIR=/app/policies/ \
#     my-agent:latest
#
# Deploy to ECS Fargate: see ecs-task-def.json in this directory.
# Deploy to Lambda container: change ENTRYPOINT to use lambda_handler.
# ─────────────────────────────────────────────────────────────────────────────

# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir hatchling

# Copy the arc monorepo packages needed for an agent runtime
COPY tollgate/ ./tollgate/
COPY arc/packages/arc-core/ ./arc-core/
COPY arc/packages/arc-runtime/ ./arc-runtime/
COPY arc/packages/arc-connectors/ ./arc-connectors/
COPY arc/packages/arc-orchestrators/ ./arc-orchestrators/
COPY arc/packages/arc-cli/ ./arc-cli/

# Build wheels for each package
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels \
      ./tollgate ./arc-core ./arc-runtime ./arc-connectors \
      ./arc-orchestrators ./arc-cli

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security (important for regulated environments)
RUN groupadd --gid 1001 arc && \
    useradd --uid 1001 --gid arc --shell /bin/bash --create-home arc

WORKDIR /app

# Install the pre-built wheels and runtime extras
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir \
      "tollgate[aws]" \
      "arc-core" \
      "arc-runtime[aws]" \
      "arc-connectors[aws]" \
      "arc-orchestrators" \
      "arc-cli" \
      --find-links /wheels && \
    rm -rf /wheels

# Copy agent artifacts (manifest, policies, agent code)
# In a real image build, your team repo's CI copies these in.
# Pattern: COPY agents/my-agent/ /app/
COPY . /app/

# ── Environment ───────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARC_ENV=sandbox \
    ARC_MANIFEST_PATH=/app/manifest.yaml \
    ARC_POLICY_DIR=/app/policies/ \
    ARC_LOG_LEVEL=INFO

# Build args — set the agent module and class to run
ARG AGENT_MODULE=my_agents.agent
ARG AGENT_CLASS=MyAgent
ENV AGENT_MODULE=${AGENT_MODULE} \
    AGENT_CLASS=${AGENT_CLASS}

# Drop to non-root
USER arc

# Healthcheck — arc CLI validates the manifest on startup
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD arc agent validate ${ARC_MANIFEST_PATH} || exit 1

# Default: dry-import the agent class so the container fails fast on import errors.
# Override CMD in ECS task definition for specific trigger modes.
CMD ["python", "-c", \
     "import importlib, os; \
      mod = importlib.import_module(os.environ['AGENT_MODULE']); \
      cls = getattr(mod, os.environ['AGENT_CLASS']); \
      from arc.runtime.deploy.lambda_handler import make_handler; \
      print('Agent container ready:', cls.__name__)"]
