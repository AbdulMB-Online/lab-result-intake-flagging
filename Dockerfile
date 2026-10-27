# Match the Python version used by CI.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    LAB_DB_PATH=/data/lab.db \
    LAB_SETTINGS_PATH=/config/settings.json

WORKDIR /app

# The agreed schema uses STRICT tables, introduced in SQLite 3.37.
RUN python -c "import sqlite3; assert sqlite3.sqlite_version_info >= (3, 37, 0), 'SQLite 3.37 or newer is required'"

# Give the application a writable database directory and its own user.
RUN groupadd --system lab \
    && useradd --system --gid lab --no-create-home lab \
    && mkdir -p /data /backups /config /inputs \
    && chown lab:lab /data /backups

# Runtime dependencies are currently all in Python's standard library.
# Settings and input files are supplied through read-only bind mounts.
COPY src/ ./src/

USER lab

# Mount a named volume here when running to retain history between containers.
VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "lab_intake"]
CMD ["--help"]
