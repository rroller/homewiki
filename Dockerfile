FROM python:3.15-slim
ARG HOMEWIKI_REVISION=unknown
WORKDIR /app
COPY server.py bookmarks.py backup.py ./
COPY static ./static
RUN printf '%s\n' "$HOMEWIKI_REVISION" > /app/REVISION && useradd --uid 10001 --create-home wiki && mkdir /data && chown wiki:wiki /data
USER wiki
ENV HOMEWIKI_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/pages', timeout=2)"
CMD ["python", "server.py", "--host", "0.0.0.0"]
