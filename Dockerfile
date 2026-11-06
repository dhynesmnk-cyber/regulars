# Regulars - one venue, one container.
# Pure Python standard library: no pip install, no build step, small attack surface.
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 regulars
WORKDIR /app

COPY app/ /app/app/
COPY start.sh README.md DEPLOYMENT.md CHANGELOG.md /app/

# State, audit log and the encryption keyfile live on a volume so a redeploy
# never loses the venue's config, the security history, or the token key.
RUN mkdir -p /data && chown -R regulars:regulars /data /app
VOLUME /data

ENV REGULARS_STATE=/data/regulars-state.json \
    REGULARS_AUDIT_LOG=/data/regulars-audit.log \
    REGULARS_KEYFILE=/data/regulars-master.key \
    REGULARS_TLS=1 \
    REGULARS_BIND=0.0.0.0 \
    REGULARS_TRUST_PROXY=1 \
    PORT=8010

# Listening on 0.0.0.0 is only safe because the port is published on the
# host's loopback (docker run -p 127.0.0.1:8010:8010) and a TLS proxy on the
# host is the only thing that reaches it. REGULARS_TRUST_PROXY=1 then takes
# the client address from the entry that proxy adds to X-Forwarded-For.

# Secrets are injected at runtime, never baked into the image:
#   REGULARS_MASTER_KEY, REGULARS_SETUP_TOKEN, SQUARE_CLIENT_ID,
#   SQUARE_CLIENT_SECRET, REGULARS_REDIRECT_URI
USER regulars
EXPOSE 8010
WORKDIR /app/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8010'), timeout=4)"

CMD ["sh", "-c", "exec python3 server.py ${PORT:-8010}"]
