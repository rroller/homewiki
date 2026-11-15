FROM python:3.14-slim
WORKDIR /app
COPY server.py ./
COPY static ./static
RUN useradd --uid 10001 --create-home wiki && mkdir /data && chown wiki:wiki /data
USER wiki
ENV HOMEWIKI_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/pages', timeout=2)"
CMD ["python", "server.py", "--host", "0.0.0.0"]
