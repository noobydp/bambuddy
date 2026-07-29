# Bambuddy — FlashForge and Klipper fork

**Maintained fork of
[`maziggy/bambuddy`](https://github.com/maziggy/bambuddy), extending the
self-hosted print archive and management system to FlashForge LAN and
Klipper/Moonraker printers.**

No cloud dependency. Complete privacy. Full control.

[![GitHub](https://img.shields.io/github/stars/noobydp/bambuddy?style=flat-square&label=Fork)](https://github.com/noobydp/bambuddy)
[![License](https://img.shields.io/github/license/noobydp/bambuddy?style=flat-square)](https://github.com/noobydp/bambuddy/blob/main/LICENSE)
[![Discord](https://img.shields.io/discord/1461241694715645994?style=flat-square&logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/aFS3ZfScHM)

This is an independent fork. The Discord server, website, and wiki belong to
the upstream Bambuddy project.

## Quick Start

```bash
mkdir bambuddy && cd bambuddy
curl -O https://raw.githubusercontent.com/noobydp/bambuddy/main/docker-compose.yml
docker compose up -d
```

Open **http://localhost:8000** and add your printer.

> **Requirements:** A supported Bambu Lab, FlashForge LAN, or
> Klipper/Moonraker printer reachable from the Bambuddy host.

## Supported Architectures

| Architecture | Tag |
|---|---|
| x86-64 (Intel/AMD) | `amd64` |
| arm64 (Raspberry Pi 4/5) | Build from source; no pre-built fork image yet |

## Features

- **Real-Time Monitoring** — Live printer status, camera streaming, HMS error tracking (853 codes translated), resizable multi-printer dashboard
- **Print Archive** — Automatic 3MF archiving with metadata, interactive 3D model viewer (Three.js), photo attachments, failure analysis, side-by-side comparison
- **Print Scheduling** — Drag-and-drop queue, multi-printer assignment by model or location, time-based scheduling, re-print with AMS mapping
- **Smart Automation** — Smart plug control (Tasmota, Home Assistant, MQTT), auto power-on/off, energy monitoring, maintenance reminders
- **Proxy Mode** — Print remotely from Bambu Studio/OrcaSlicer without VPN or port forwarding, end-to-end TLS encrypted
- **Notifications** — WhatsApp, Telegram, Discord, Email, Pushover, ntfy with customizable templates and quiet hours
- **Projects** — Group related prints, track parts and plates, bill of materials, cost tracking, export as ZIP/JSON
- **File Manager** — Upload and organize sliced files, folder structure, print directly to any printer
- **Integrations** — Spoolman filament sync, MQTT publishing, Prometheus metrics, Bambu Cloud profiles, REST API, Home Assistant
- **Virtual Printer** — Appears in your slicer via SSDP discovery, multiple operating modes (archive, review, queue, proxy)
- **Security** — Optional authentication with group-based permissions (50+ granular), JWT tokens, API key support

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TZ` | `UTC` | Timezone (e.g. `America/New_York`, `Europe/Berlin`) |
| `PORT` | `8000` | Web UI port |
| `PUID` | `1000` | User ID for file permissions |
| `PGID` | `1000` | Group ID for file permissions |
| `DEBUG` | `false` | Enable debug logging |

## Volumes

| Path | Purpose |
|---|---|
| `/app/data` | Database, archived prints, thumbnails |
| `/app/logs` | Application logs |

## Docker Compose

```yaml
services:
  bambuddy:
    image: ghcr.io/noobydp/bambuddy:latest
    container_name: bambuddy
    network_mode: host
    environment:
      - TZ=America/New_York
      - PUID=1000
      - PGID=1000
    volumes:
      - bambuddy_data:/app/data
      - bambuddy_logs:/app/logs
    restart: unless-stopped

volumes:
  bambuddy_data:
  bambuddy_logs:
```

> **macOS/Windows:** Docker Desktop doesn't support `network_mode: host`. Replace it with `ports: ["8000:8000"]` and add printers manually by IP.

## Updating

```bash
docker compose pull && docker compose up -d
```

## Fork Tags

The combined build is published from `main`:

```bash
docker pull ghcr.io/noobydp/bambuddy:latest
```

`flashforge-creator5pro` and `klipper-moonraker` are compatibility aliases for
the same image. Use `latest` for the complete fork.

## Supported Printers

| Provider | Models / systems | Status |
|---|---|---|
| Bambu Lab | Models supported by upstream Bambuddy | Upstream-compatible |
| FlashForge LAN | Creator 5 Pro confirmed | Experimental |
| Klipper / Moonraker | TinyT and Trident confirmed; other Moonraker systems use capability discovery | Experimental |

## Links

- **Fork:** [github.com/noobydp/bambuddy](https://github.com/noobydp/bambuddy)
- **Fork policy:** [FORK.md](https://github.com/noobydp/bambuddy/blob/main/FORK.md)
- **Fork issues:** [GitHub Issues](https://github.com/noobydp/bambuddy/issues)
- **Upstream:** [github.com/maziggy/bambuddy](https://github.com/maziggy/bambuddy)
- **Upstream documentation:** [wiki.bambuddy.cool](https://wiki.bambuddy.cool)
- **Upstream Discord:** [discord.gg/aFS3ZfScHM](https://discord.gg/aFS3ZfScHM)

## License

GNU AGPL v3 - see
[LICENSE](https://github.com/noobydp/bambuddy/blob/main/LICENSE) for details.
