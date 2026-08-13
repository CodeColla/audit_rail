# Audit Rail — API image.
#
# Build from the REPO ROOT, not from this directory:
#     docker build -f _docker/dockerfile/audit-rail-api.Dockerfile -t audit-rail-api:TAG .
# (_docker/scripts/service_ctl.sh build 1 does this for you and works out the tag.)
#
# `python:3.12-slim`, not alpine, on purpose: Pillow, psycopg[binary], reportlab and lxml all
# publish manylinux wheels and no musl ones, so alpine would compile them from source — a much
# larger toolchain in the image and a far slower build, for no runtime benefit.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first, so a code change does not re-run pip. Installed verbatim rather than
# through a filtered "prod-only" list: pytest and httpx ride along (~10 MB) and that is a
# cheaper price than a second requirements file drifting out of step with the real one.
COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

# `db/` and `scripts/` are here so the schema and the maintenance scripts can be run FROM the
# container — `docker compose exec api python scripts/init_db.py --blank`, for instance —
# rather than needing a checkout on the server.
COPY api/    /app/api/
COPY db/     /app/db/
COPY scripts/ /app/scripts/

# The vault lives on a MOUNTED VOLUME, never in the image. Its default is inside the source
# tree (api/config.py), which in a container means the image layer — so every redeploy would
# silently destroy every uploaded file while leaving the `files` rows behind, and the UI would
# list evidence that 410s when clicked. Setting it here makes that impossible to forget.
ENV VAULT_DIR=/data/vault
RUN mkdir -p /data/vault

# Run as a non-root user that owns the vault mountpoint.
RUN useradd --system --create-home --uid 10001 auditrail \
 && chown -R auditrail:auditrail /data /app
USER auditrail

EXPOSE 5007
VOLUME ["/data/vault"]

# No curl in slim, and adding it just for this is silly — Python is already here.
# `GET /` is the health endpoint (api/main.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5007/', timeout=4).status == 200 else 1)"

# --proxy-headers so request.client.host is the real client rather than the proxy. NO --reload:
# that is a dev-only flag and it forks a child process, which breaks container signal handling
# and doubles the APScheduler.
CMD ["sh", "-c", "exec uvicorn api.main:app \
  --host 0.0.0.0 --port 5007 \
  --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-*}\" \
  --workers ${WEB_CONCURRENCY:-1}"]
