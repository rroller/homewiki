# Unraid deployment

Run `deploy-unraid.sh` on Unraid from this source folder. It creates a new `homewiki` container and `/mnt/user/appdata/homewiki` deployment, refusing to replace an existing container or directory. It builds the local Dockerfile; the initial build needs internet access for the Python base image.

## Unraid template and published image

The Unraid template is in [rroller/unraid-templates](https://github.com/rroller/unraid-templates/blob/master/ronnieroller.com/homewiki.xml). It uses `ghcr.io/rroller/homewiki:latest`, built for `linux/amd64` from this repository's `main` branch. Runtime changes trigger publishing after the storage/HTTP suite and a non-root, read-only container smoke test pass. A manual workflow run can also rebuild the image. Each build additionally publishes a `sha-<full-commit>` tag.

Before the first template install, set **Allowed hosts** to your Unraid LAN IP or hostname (comma-separated, no scheme or port). Map `/data` to `/mnt/user/appdata/homewiki/data` and keep that folder across upgrades. Image uploads live in its `uploads/` subfolder; they use this same persistent bind mount and must be included in backups. For a fresh deployment, create it on Unraid with writable ownership for UID/GID 10001:

```sh
install -d -m 0700 -o 10001 -g 10001 /mnt/user/appdata/homewiki/data
```

For an existing deployment, reuse its data folder and preserve its ownership. Do not start two containers against the same wiki data directory. The template does not migrate or replace an existing container automatically. The host port defaults to 18080; the app has no authentication and should remain on a trusted private network.

The source-build deployment script below remains available for users who prefer local images.

## Default: tunnel-only access

```sh
sh deploy-unraid.sh
```

The container binds only `127.0.0.1:18080`. From your computer:

```sh
ssh -N -L 18080:127.0.0.1:18080 your-user@your-unraid
```

Open http://localhost:18080.

## Private LAN access

Replace `YOUR_LAN_IP` with the Unraid server's LAN IP in both settings:

```sh
HOMEWIKI_BIND_ADDRESS=YOUR_LAN_IP HOMEWIKI_ALLOWED_HOSTS=YOUR_LAN_IP sh deploy-unraid.sh
```

Open `http://YOUR_LAN_IP:18080`. Homewiki has no authentication; any device that can reach this endpoint can read and edit pages. Keep the port off the public internet.

`HOMEWIKI_PORT` overrides 18080. `HOMEWIKI_APPDATA_ROOT` overrides `/mnt/user/appdata`. The script does not modify other containers, proxies, or router settings. Failures preserve newly created files for inspection.

## Files and management

- `appdata/homewiki/data`: persistent pages, recoverable trash, and initialization state.
- `appdata/homewiki/source`: deployed application source.
- `appdata/homewiki/image.txt`: local image tag.
- Container: `homewiki`, non-root UID 10001, restart policy `unless-stopped`.

The application filesystem is read-only, `/tmp` is temporary, Linux capabilities are dropped, and privilege escalation is disabled. The container appears in Unraid's Docker list with a WebUI label. Its locally built image is not updated from a registry.

```sh
docker logs --tail 50 homewiki
docker inspect --format '{{.State.Health.Status}}' homewiki
docker stop homewiki
docker start homewiki
```

Back up the complete `data` folder with Homewiki stopped, including `.trash` and hidden files. Preserve UID/GID 10001 ownership. Keep the data folder when updating or recreating the container. Do not put the appdata folder or backups into Git.

For updates, build the desired source as a new local image, then recreate only the Homewiki container with its existing data bind mount, port, allowed Host, and security options. The new-deployment script deliberately does not replace an existing deployment. Keep the previous image for rollback until the new container is verified.

The generic `compose.yaml` is a separate localhost-only deployment using a named volume. It does not automatically use an existing Unraid bind mount; do not start it alongside the scripted deployment expecting the same data store.
