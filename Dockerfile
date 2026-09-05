# HELIOS — the deterministic, execution-blind strategy-state engine.
#
# One image, promotable unchanged from the development host to a production
# one. Everything that differs between environments is injected at run time:
# where the facts come from, where strategy state is published, how fresh a
# fact may be, how often to evaluate. Nothing operational is baked in here, and
# there is no credential anywhere in this file or in anything it copies —
# HELIOS needs none to evaluate strategies against market facts.
#
# See docs/DEPLOYMENT.md for how to build and run it, and docs/RUNTIME.md for
# what the service does once it is running.

# The base is pinned by DIGEST, not by tag. A tag is a moving reference: the
# same Dockerfile built a month apart would produce different images and
# HELIOS's determinism claim is about a build, not about a name.
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS build

WORKDIR /src

# Only what a distribution is built from. The build stage is discarded, so no
# compiler, cache or source tree reaches the image that ships.
COPY pyproject.toml ./
COPY helios ./helios

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install --no-cache-dir .


FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

LABEL org.opencontainers.image.title="HELIOS" \
      org.opencontainers.image.description="Deterministic, execution-blind strategy-state engine." \
      org.opencontainers.image.licenses="UNLICENSED"

COPY --from=build /opt/venv /opt/venv

# The v1 market-fact feed and the HSA strategy handoff ship as image DATA. They
# are not configuration: which directory the service reads is configuration,
# and a production deployment points HELIOS_RUNTIME_FEED_DIR and
# HELIOS_STRATEGY_PACKAGE_DIR at mounted volumes instead, with no rebuild.
COPY fixtures/hermes/scenario /opt/helios/facts
COPY fixtures/strategy_packages/scenario /opt/helios/strategies

# Runs as a non-root user with no login shell. The one writable path is the
# state directory, so the rest of the filesystem can be mounted read-only.
RUN groupadd --system --gid 10001 helios \
    && useradd --system --uid 10001 --gid helios --home-dir /var/lib/helios \
       --shell /usr/sbin/nologin helios \
    && mkdir -p /var/lib/helios \
    && chown -R helios:helios /var/lib/helios

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001
WORKDIR /var/lib/helios
VOLUME ["/var/lib/helios"]

# Health and readiness without a listening socket. The probe reads the same
# configuration the service does, so there is no second source of truth for
# where the status document lives or how stale it may be — and no port is bound,
# which is what the PID requires by default.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python3", "-m", "helios.runtime.health"]

# No arguments: the service takes its whole configuration from the environment.
# Result codes: 0 clean stop, 1 failed while running, 2 refused to start.
CMD ["python3", "-m", "helios.runtime"]
