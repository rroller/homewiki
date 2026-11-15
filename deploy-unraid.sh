#!/bin/sh
# Run on Unraid, from the extracted Homewiki source. Creates a NEW deployment only.
set -eu
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APPDATA_ROOT=${HOMEWIKI_APPDATA_ROOT:-/mnt/user/appdata}
DEPLOY_DIR="$APPDATA_ROOT/homewiki"
PORT=${HOMEWIKI_PORT:-18080}
BIND_ADDRESS=${HOMEWIKI_BIND_ADDRESS:-127.0.0.1}
ALLOWED_HOSTS=${HOMEWIKI_ALLOWED_HOSTS:-}
CONTAINER=homewiki

case "$PORT" in ''|*[!0-9]*) echo 'HOMEWIKI_PORT must be numeric' >&2; exit 1;; esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
    echo 'HOMEWIKI_PORT must be between 1024 and 65535' >&2; exit 1
fi
command -v docker >/dev/null || { echo 'Docker is not installed' >&2; exit 1; }
docker info >/dev/null
[ -d "$APPDATA_ROOT" ] || { echo "Appdata directory does not exist: $APPDATA_ROOT" >&2; exit 1; }
[ -f "$SOURCE_DIR/server.py" ] && [ -f "$SOURCE_DIR/Dockerfile" ] || { echo 'Run this script from the Homewiki source folder' >&2; exit 1; }
if [ -e "$DEPLOY_DIR" ] || [ -L "$DEPLOY_DIR" ]; then
    echo "Refusing to replace existing deployment: $DEPLOY_DIR" >&2; exit 1
fi
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "Refusing to replace existing container: $CONTAINER" >&2; exit 1
fi
# mkdir without -p refuses races with another deployment. Only these new files are modified.
umask 077
mkdir "$DEPLOY_DIR"
mkdir "$DEPLOY_DIR/source" "$DEPLOY_DIR/data"
tar -C "$SOURCE_DIR" --exclude='./data' --exclude='./__pycache__' --exclude='./tests' --exclude='./.venv' -cf - . | tar -C "$DEPLOY_DIR/source" -xf -
chown 10001:10001 "$DEPLOY_DIR/data"
chmod 700 "$DEPLOY_DIR/data"
IMAGE="homewiki:local-$(date -u +%Y%m%dT%H%M%SZ)-$$"
docker build --tag "$IMAGE" "$DEPLOY_DIR/source"
docker run --detach \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --env "HOMEWIKI_ALLOWED_HOSTS=$ALLOWED_HOSTS" \
    --label "net.unraid.docker.webui=http://[IP]:[PORT:8080]/" \
    --publish "$BIND_ADDRESS:$PORT:8080" \
    --mount "type=bind,source=$DEPLOY_DIR/data,target=/data" \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --label net.unraid.docker.managed=dockerman \
    "$IMAGE"
printf '%s\n' "$IMAGE" > "$DEPLOY_DIR/image.txt"
printf 'Homewiki container started. Checking health'
ATTEMPTS=0
while [ "$ATTEMPTS" -lt 40 ]; do
    HEALTH=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER")
    if [ "$HEALTH" = healthy ]; then
        printf '\nHealthy. Private endpoint: http://%s:%s\nData: %s/data\n' "$BIND_ADDRESS" "$PORT" "$DEPLOY_DIR"
        if [ "$BIND_ADDRESS" = 127.0.0.1 ]; then
            printf 'On your computer, tunnel with: ssh -N -L %s:127.0.0.1:%s USER@UNRAID\n' "$PORT" "$PORT"
        fi
        exit 0
    fi
    if [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" != true ]; then break; fi
    ATTEMPTS=$((ATTEMPTS+1))
    sleep 2
    printf '.'
done
printf '\nHealth check did not pass. Deployment files were preserved at %s.\n' "$DEPLOY_DIR" >&2
docker logs --tail 30 "$CONTAINER" >&2
exit 1
