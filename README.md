# Homewiki

A lightweight, locally hosted personal wiki. Pages are ordinary UTF-8 Markdown files in nested folders, without a database, build step, or third-party runtime dependencies.

- Collapsible sidebar, light/dark themes, and clean page URLs with clickable breadcrumbs.
- Create, edit, rename/move, and delete pages in the browser.
- Markdown editor with preview, internal wiki links, a page-link picker, and full-text search.
- Browser draft recovery, revision-based conflict detection, and a manual merge view.
- Recoverable deleted pages, last-edit dates, and a Recently edited index.
- Docker Compose and an optional Unraid deployment script with persistent storage.

## Run locally

Requires Python 3.10+ on Linux or macOS (`fcntl` provides the file lock).

```sh
python3 server.py
```

Open **http://localhost:8080**. Stop with Ctrl+C. The default listener is `127.0.0.1`; data lives in `data/` beside the server.

```sh
python3 server.py --port 8090 --data /path/to/my-homewiki
```

The first run seeds a few generic starter pages only if the pages folder is empty. Existing files are never seeded over. Use `--no-seed` to start with an empty wiki.

## Docker Compose

Requires Docker with the Compose plugin:

```sh
docker compose up -d --build
```

Open http://localhost:8080. Compose publishes only on `127.0.0.1`; the named `homewiki_data` volume persists pages and trash across container restarts and rebuilds. The container runs as a non-root user with a read-only app filesystem.

```sh
docker compose logs -f
docker compose down
```

`down` preserves the volume. `down -v` deletes it. The Python `data/` folder and Docker named volume are separate stores.

A published `linux/amd64` image is available as `ghcr.io/rroller/homewiki:latest`, with commit-specific `sha-<full-commit>` tags. Publishing runs the storage/HTTP tests and a read-only container smoke test before pushing.

See [Unraid instructions](UNRAID.md) for a bind-mounted appdata deployment.

## Private access

Homewiki is intended for a trusted personal environment and has no login system. Keep it off the public internet. Other programs running under your OS account can access its files.

For a remote server, retain the default localhost listener and tunnel from your computer:

```sh
ssh -N -L 8080:127.0.0.1:8080 your-user@your-server
```

For intentional access from trusted devices on a private LAN, bind to the server's actual LAN IP and allow that exact Host:

```sh
python3 server.py --host YOUR_LAN_IP --port 18080 --allow-host YOUR_LAN_IP
```

Docker deployments can set `HOMEWIKI_ALLOWED_HOSTS` to the permitted IP/hostname and publish on that specific LAN address. Anyone who can reach the permitted endpoint can read and edit the wiki. Host/Origin checks and the mutation header protect against cross-origin browser requests; they are not authentication.

## Use the wiki

Create nested pages with paths such as `Games/Completed games`. A folder may also have its own page: `Games.md` beside `Games/Completed games.md`.

Use `[[Games/Completed games]]` or `[[Games/Completed games|Finished games]]` to link from the wiki root. The editor's page-link picker inserts these links. Ordinary Markdown links resolve relative to the current page's folder; begin the target with `/` for a wiki-root-relative link. Paths are case-sensitive. Missing pages offer to create them.

Page URLs use `/page/Home` or `/page/Games/Completed%20games`. Direct links, refresh, and browser back/forward work. Older `/#/page/...` links open at their equivalent clean URL. Breadcrumb ancestors link back to parent pages and Home.

Ctrl/Cmd+K opens search; Ctrl/Cmd+S saves an edit. Closing the editor retains its browser draft; Discard draft removes it. Rename/move changes only the selected Markdown file. Children and links to its old path remain unchanged, and existing destinations are never replaced.

Delete moves a page into Recently deleted. Children remain accessible. Restore it through the sidebar; if the original path is occupied, move that replacement first. Trash has no automatic expiry or permanent-delete UI.

The footer uses the Markdown file's modification time for its last-edit date, displayed in the browser's local time zone. Recently edited (`/recently-edited`) is a generated index ordered newest first. It includes external file edits, excludes deleted pages, and is not itself an editable Markdown file. Renaming or restoring a page preserves its content edit date.

The small Markdown renderer supports headings, paragraphs, bold, emphasis, strikethrough, inline/fenced code, basic lists, read-only task lists, blockquotes, tables, horizontal rules, and links. It is not full CommonMark: nested list formatting, inline images, raw HTML, and extensions are not rendered. HTML appears as text; remote images are not requested. External HTTP(S) links open separately when clicked.

## Storage and durability

```text
data/
  pages/
    Home.md
    Games.md
    Games/
      Completed games.md
  .trash/<unique-id>/
    page.md
    metadata.json
  .initialized
  .lock
```

**Keep personal wiki content outside Git.** This repository contains application source and documentation only. The ignore rules exclude Markdown except the explicitly allowed root documentation, plus runtime data, trash, backups, and local configuration. The Docker build context allows only runtime source. For a custom data directory, prefer a path outside this checkout. Never force-add wiki data or backup files.

You can edit UTF-8 `.md` files with any text editor. The app discovers them on refresh and polls every ten seconds while visible. Hidden names, traversal paths, and symlinks are rejected. Each page/request is limited to approximately 2 MiB. Search scans the files and is intended for a small personal collection.

Saves flush a temporary file and atomically replace the page. A cross-process advisory lock serializes UI mutations. SHA-256 revisions reject stale saves, renames, and deletes. The conflict dialog shows the current file, your draft, an editable merged draft, and a draft download. Accepting a merge updates its base revision; Save checks again. External editors do not acquire the advisory lock, so avoid simultaneous external writes during a UI save.

Unsaved edits go immediately into localStorage with separate keys for tabs. Revisiting a page or reloading recovers a draft. If browser storage fails, the editor warns and prompts before leaving with unsaved work. Browser drafts belong to the origin, including its port; clearing site data removes them. They are not server backups.

Back up the complete server-side data folder, including `.trash` and hidden files, with edits paused or the app stopped. For Docker, back up the volume or bind mount and preserve ownership (UID/GID 10001). Keep data when replacing the container. A volume copy while the container is running but edits are paused can be made to a fresh destination with:

```sh
docker compose cp homewiki:/data ./homewiki-backup
```

## Tests

The storage and HTTP tests need only Python's standard library and use temporary data:

```sh
python3 -m unittest discover -s tests -p test_server.py -v
```

Optional Chromium integration tests use Playwright, needed only for testing:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install playwright
python -m playwright install --with-deps chromium
python -m unittest discover -s tests -v
```

The suite covers file storage and CRUD, destination protection, external and concurrent edits, path safety, private request guards, direct page routes, editor/preview/link/search flows, draft recovery, conflict merging, trash/restore, themes, mobile layout, safe Markdown rendering, edit dates, and recent-page ordering. Tests do not edit your real wiki.
