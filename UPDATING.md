# Updating the FlashForge and Klipper fork

This guide applies to [`noobydp/bambuddy`](https://github.com/noobydp/bambuddy).
Upstream images and source checkouts do not include this fork's FlashForge and
Klipper/Moonraker additions.

The in-app update flow originates upstream and may not preserve a fork
installation. Prefer the explicit Docker or Git commands below.

Pick the section that matches how Bambuddy was installed.

---

## Docker

```bash
# 1. Make sure your compose file isn't pinned to an old version.
#    The image line should read:
#      image: ghcr.io/noobydp/bambuddy:latest
#    An upstream ghcr.io/maziggy image will not contain fork features.

# 2. Pull and restart
docker compose pull
docker compose up -d
```

If your compose file predates the fork, download the current fork version and
compare it with your local paths, ports, and environment variables:

```bash
curl -fsSL https://raw.githubusercontent.com/noobydp/bambuddy/main/docker-compose.yml \
  -o docker-compose.yml.new
# Diff against yours, merge by hand, then:
docker compose up -d
```

---

## Native install (`install.sh` or manual `git clone`)

Both paths produce a git working tree at the install directory, so the update
is the same. Confirm that `origin` points to `noobydp/bambuddy` before updating.
Preferred:

```bash
sudo /opt/bambuddy/install/update.sh
```

`update.sh` stops the service, snapshots the database via the built-in backup
API, fast-forwards to the fork's `origin/main`, installs dependencies, rebuilds
the frontend, and restarts the service. It rolls back automatically if a step
fails.

### Manual equivalent

If you'd rather run the steps yourself:

```bash
cd /opt/bambuddy
sudo systemctl stop bambuddy
sudo -u bambuddy git fetch origin
sudo -u bambuddy git reset --hard origin/main
sudo -u bambuddy venv/bin/pip install -r requirements.txt
sudo systemctl start bambuddy
```

Replace `/opt/bambuddy` with your install path if different. Database schema
migrations run automatically on startup — no Alembic step is required.

---

## Installed from a GitHub ZIP or tarball download

These installs have no `.git` directory, so neither `update.sh` nor a plain
`git pull` will work. Reinstall cleanly:

```bash
# 1. Back up your stateful data
sudo systemctl stop bambuddy
sudo tar czf ~/bambuddy-backup.tgz -C /opt/bambuddy \
  data bambuddy.db bambuddy.db-shm bambuddy.db-wal \
  virtual_printer archive projects icons .env 2>/dev/null || true

# 2. Remove the old install and reinstall via install.sh
sudo rm -rf /opt/bambuddy
curl -fsSL https://raw.githubusercontent.com/noobydp/bambuddy/main/install/install.sh \
  -o /tmp/install.sh && sudo bash /tmp/install.sh --path /opt/bambuddy

# 3. Restore your data
sudo systemctl stop bambuddy
sudo tar xzf ~/bambuddy-backup.tgz -C /opt/bambuddy
sudo systemctl start bambuddy
```

---

## Maintainer: merging upstream changes

The fork is intended to continue receiving changes from
`maziggy/bambuddy:main`. A normal synchronization starts with:

```bash
git remote add upstream https://github.com/maziggy/bambuddy.git  # first time only
git fetch upstream
git checkout main
git merge upstream/main
```

Resolve conflicts in favor of current upstream architecture while preserving
the provider abstractions and FlashForge/Klipper capabilities. Run the backend,
frontend, and Docker validation before pushing the merge. Do not force-push
`main`; a regular merge commit keeps the upstream relationship visible and
future synchronizations understandable.

---

## Before you upgrade

Take a backup. Settings → Backup → **Create Backup** downloads a ZIP containing
the database and all stateful directories. Any bare-metal update via
`update.sh` does this automatically; Docker and manual upgrades do not.
