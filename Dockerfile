FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMAILCALL_HOST=0.0.0.0 \
    EMAILCALL_PORT=10086 \
    EMAILCALL_DATA_DIR=/data \
    AGENTCALL_DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agentcall \
    && useradd --uid 10001 --gid 10001 --no-create-home agentcall \
    && mkdir -p /data \
    && chown -R agentcall:agentcall /data \
    && chmod 700 /data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=agentcall:agentcall gateway/ ./gateway/
COPY --chown=agentcall:agentcall frontend/ ./frontend/
COPY --chown=agentcall:agentcall skills/ ./skills/

USER agentcall
EXPOSE 10086
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10086/api/health', timeout=2).read()"
CMD ["python", "-m", "gateway"]
