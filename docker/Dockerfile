# Kehrwoche — one image, one process, no build tools in what ships.
#
# The builder resolves and installs everything into a virtual environment; the runtime
# stage copies that environment and nothing else. Compilers, headers and package caches
# stay behind in a layer that is never published.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

# Only what the build needs: the metadata and the package itself. Everything else —
# tests, migrations, development databases — is irrelevant to the installed artefact.
COPY pyproject.toml README.md LICENSE ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install . \
    # Nothing installs anything at runtime, so the installer itself can go (~15 MB).
    && /opt/venv/bin/pip uninstall --yes pip setuptools \
    # Pre-compiled bytecode is another ~31 MB that the interpreter can derive again in
    # about a second. It could not be reused anyway: the process runs unprivileged and
    # must not be able to write into its own code.
    && find /opt/venv -name "__pycache__" -prune -exec rm -rf {} +


FROM python:3.12-slim AS runtime

# An unprivileged account, created before anything belongs to it.
RUN useradd --system --create-home --uid 10001 --shell /usr/sbin/nologin kehrwoche

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Migrations are not part of the package: they belong to the instance, not to the code.
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# The data volume is created here so it carries the right owner when Docker mounts it.
RUN mkdir -p /data && chown kehrwoche:kehrwoche /data

USER kehrwoche

# The default mode is TLS with a self-signed certificate, which listens on 8443.
EXPOSE 8443

# The probe asks the instance over the loopback interface, in whatever mode it runs.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.startup", "healthcheck"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
