# Homewiki on Unraid

Use the [Homewiki template](https://github.com/rroller/unraid-templates/blob/master/ronnieroller.com/homewiki.xml) and the published `ghcr.io/rroller/homewiki:latest` image for normal Unraid installation and updates. The image supports `linux/amd64` and runs as UID/GID 10001.

## Install with the Unraid template

### 1. Prepare persistent storage

Open **Unraid → Terminal**. For a fresh installation, create the data directory:

```sh
install -d -m 0700 -o 10001 -g 10001 /mnt/user/appdata/homewiki/data
```

For an existing wiki, reuse its data folder and preserve its contents and ownership. The image needs write access as UID/GID 10001. Mount the complete data root, not just its `pages` folder.

### 2. Install the user template

Run in the Unraid terminal:

```sh
homewiki_template=/boot/config/plugins/dockerMan/templates-user/my-homewiki.xml
if [ -e "$homewiki_template" ]; then
  printf '%s\n' 'Homewiki already has a user template. Use Docker > homewiki > Edit.'
else
  homewiki_download=$(mktemp)
  if curl --fail --location \
    https://raw.githubusercontent.com/rroller/unraid-templates/master/ronnieroller.com/homewiki.xml \
    --output "$homewiki_download"; then
    cp -n "$homewiki_download" "$homewiki_template"
  fi
  rm -f "$homewiki_download"
fi
```

This keeps an existing configured template intact. The app does not need to be listed in Community Applications to use this method.

### 3. Configure and start Homewiki

Open **Docker → Add Container**, then select **homewiki** in the **Template** dropdown. If you just installed the template, refresh the Docker page first.

| Setting | Value |
| --- | --- |
| Name | `homewiki` |
| Repository | `ghcr.io/rroller/homewiki:latest` |
| Network Type | `bridge` |
| Web port | `18080`, or another unused host port |
| Wiki data | `/mnt/user/appdata/homewiki/data` mapped read/write to `/data` |
| Allowed hosts | Your Unraid LAN IP and any hostname used to reach the wiki, comma-separated |

**Allowed hosts** takes exact hostnames/IPs without a scheme, path, or port. For example: `192.168.1.10,wiki.example.com`. Localhost is always allowed. If you access Homewiki through a reverse proxy, include the hostname that the browser uses and preserve the intended private network/access controls.

Click **Apply**. When the container is running, use its **WebUI** menu item or open `http://YOUR_UNRAID_IP:18080/Home`. Turn on its **Autostart** toggle if it should start with Unraid.

The template provides a read-only app filesystem, a temporary `/tmp`, dropped Linux capabilities, and no privilege escalation. Homewiki has no login system; anyone who can reach it can read and edit pages. Use a trusted private network or separate access controls.

## Storage, updates, and backups

Everything durable is inside the host folder mapped to `/data`:

```text
data/
  pages/       Markdown pages in nested folders
  uploads/     Dropped, pasted, and selected images
  .trash/      Recoverable deleted pages
  .history/    Saved page versions and metadata
  bookmarks.sqlite3  Bookmark library and search index
  .sidebar-order.json  Shared sidebar ordering
  .initialized
  .lock
```

Bookmarks use SQLite in the existing data mapping. No separate database container or additional mount is required. Include the whole data directory in stopped-container backups.

Page history is available under **••• → Page history**. Saved versions live in .history within the same data mapping; no additional mount or configuration is needed.

Keep this entire folder when updating or recreating the container. Pages, uploaded images, and backups do not belong in the Git repository.

For a template-based install, use **Docker → Check for Updates**, then update Homewiki through Unraid's normal update controls. The `latest` image is published after source changes on `main` pass tests and a container smoke test. Building a new image does not automatically update an already-running container. No GHCR login is needed for the public image.

To pin a particular build, change **Repository** to a published `ghcr.io/rroller/homewiki:sha-<full-commit>` tag. Keep the same data mapping, allowed hosts, and port. Return to `:latest` to follow normal updates.

For a portable ZIP of your saved wiki and bookmarks, use **Backup & restore** in the Homewiki sidebar. The app can restore the ZIP directly from your browser. See the [backup and restore guide](README.md#backup-and-restore-in-homewiki).

Back up the complete data folder with Homewiki stopped, including hidden files and `.trash`. Preserve UID/GID 10001 ownership when restoring. Use your usual appdata backup process, or run a manual backup to an existing backup share:

```sh
docker stop homewiki
tar -C /mnt/user/appdata/homewiki -czf /mnt/user/backups/homewiki-$(date +%Y%m%d-%H%M%S).tar.gz data
docker start homewiki
```

Create `/mnt/user/backups` or choose another existing destination before running this example, and check that `tar` succeeds. Keep backups outside the live data folder. To restore, stop Homewiki, restore the full data root and its ownership, then start it.

## Troubleshooting

- **“Host is not allowed”**: use **Edit → Allowed hosts** to add the exact hostname/IP from the browser address, without the scheme or port, then apply the change. An environment variable change requires recreating the container through Apply, not just restarting it.
- **Permission errors**: check the `/data` host path and write access for UID/GID 10001. Correct only the wiki data folder as needed; do not recursively change unrelated appdata.
- **Empty wiki after an update**: check that `/data` still points to the original data root. A different folder starts a different wiki. Stop the container before adjusting its mapping.
- **Port already in use**: pick an unused host port while keeping container port 8080.

Useful terminal checks:

```sh
docker logs --tail 50 homewiki
docker inspect --format '{{.State.Health.Status}}' homewiki
docker inspect --format '{{json .Mounts}}' homewiki
```

## Alternative: build from source

The optional `deploy-unraid.sh` builds the Dockerfile locally and creates a **new** deployment. It refuses to replace an existing `homewiki` container or appdata directory. Its default is tunnel-only access on `127.0.0.1:18080`:

```sh
sh deploy-unraid.sh
```

From your computer, tunnel with `ssh -N -L 18080:127.0.0.1:18080 your-user@your-unraid`, then open http://localhost:18080.

For intentional LAN access, replace `YOUR_LAN_IP` with the Unraid server's actual LAN IP:

```sh
HOMEWIKI_BIND_ADDRESS=YOUR_LAN_IP HOMEWIKI_ALLOWED_HOSTS=YOUR_LAN_IP sh deploy-unraid.sh
```

`HOMEWIKI_PORT` overrides 18080. `HOMEWIKI_APPDATA_ROOT` overrides `/mnt/user/appdata`. This script's image is local and does not receive registry updates; use the template and published image for normal Unraid registry updates.

The generic `compose.yaml` is a separate localhost-only deployment with a named volume. It does not automatically use an existing Unraid appdata bind mount.
