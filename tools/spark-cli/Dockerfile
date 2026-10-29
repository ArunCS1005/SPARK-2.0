FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Test image (`docker build --target dev`). The package is installed in editable mode at the path
# the repository is mounted on (-v <repo>:/repo), so tests run the working copy without rebuilding
# and can read the repo's config/ templates.
FROM base AS dev
WORKDIR /repo/tools/spark-cli
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"
CMD ["sh", "-c", "pytest -q -p no:cacheprovider && ruff check --no-cache src tests"]

# Default target: the `spark` CLI used by compose.
FROM base AS cli
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && rm -rf /src
WORKDIR /app
ENTRYPOINT ["spark"]
CMD ["--help"]
