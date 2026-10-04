FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMAILCALL_HOST=0.0.0.0 \
    EMAILCALL_PORT=10086 \
    EMAILCALL_DATA_DIR=/data

RUN apk add --no-cache ca-certificates \
    && addgroup -g 10001 -S emailcall \
    && adduser -u 10001 -S -D -G emailcall emailcall \
    && mkdir /data \
    && chown emailcall:emailcall /data \
    && chmod 700 /data

WORKDIR /app
COPY --chown=emailcall:emailcall gateway/ ./gateway/
COPY --chown=emailcall:emailcall frontend/ ./frontend/
COPY --chown=emailcall:emailcall skills/ ./skills/

USER emailcall
EXPOSE 10086
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10086/api/health', timeout=2).read()"
CMD ["python", "-m", "gateway"]
