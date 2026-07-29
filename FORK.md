# About this Bambuddy fork

This repository is an independent fork of
[`maziggy/bambuddy`](https://github.com/maziggy/bambuddy). The upstream project
provides the foundation, most features, and the public Bambuddy documentation.
This fork is maintained separately and is not an official upstream release.

## Purpose

The fork exists to make Bambuddy useful across a mixed local print farm while
preserving the upstream Bambu Lab experience.

Its current priorities are:

1. First-class FlashForge LAN support, initially validated with the Creator 5
   Pro.
2. First-class Klipper support through Moonraker, initially validated with
   modern TinyT and Trident installations.
3. Provider-neutral APIs, state, files, cameras, queueing, slicing, and controls
   so future printer integrations do not require Bambu-specific model checks.
4. Continued integration of upstream Bambuddy fixes and features.

## Upstream relationship

The intention is to merge `maziggy/bambuddy:main` into this fork regularly.
There is no fixed synchronization schedule, but long-lived divergence is not a
goal.

When upstream and fork changes overlap:

- Unmodified upstream behavior should be retained wherever possible.
- Shared behavior should be implemented through provider capabilities rather
  than printer-brand conditionals.
- Fork-specific provider support should remain fail-closed when a printer does
  not advertise a capability.
- Upstream bug fixes should be merged without unnecessary rewrites, making
  future synchronization easier.

The fork may temporarily trail upstream while conflicts, migrations, or
provider regressions are tested. The current commit history and Actions status
are the source of truth; an upstream version badge alone does not describe the
fork's exact state.

## Provider scope

| Provider | Current scope | Maturity |
|----------|---------------|----------|
| Bambu Lab | Features inherited from upstream, plus provider-neutral adaptations needed by this fork | Established upstream support |
| FlashForge LAN | Discovery/manual setup, status, cameras, files, upload/start, print controls, temperatures, lights, speed, thumbnails, and notifications where supported by the printer | Experimental; Creator 5 Pro confirmed |
| Klipper / Moonraker | Dynamic object discovery, status, cameras, files, queue dispatch, print/heater/fan/motion controls, macros, console, sensors, leveling, and toolchanger state where advertised | Experimental; TinyT and Trident confirmed |

Experimental providers are intended for daily use on the confirmed hardware,
but they have less model and firmware coverage than Bambu Lab support. A
capability may be hidden when the connected printer or Moonraker instance does
not expose it.

## Builds and installation

The combined fork image is:

```text
ghcr.io/noobydp/bambuddy:latest
```

It is currently published for `linux/amd64` after successful pushes to `main`.
The `flashforge-creator5pro` and `klipper-moonraker` tags are compatibility
aliases for the same combined image.

Use the fork's [README](README.md#-quick-start) and
[updating guide](UPDATING.md). Installing an image, compose file, installer, or
source tree from `maziggy/bambuddy` gives you upstream Bambuddy without this
fork's provider additions.

## Issues and contributions

Use the [fork issue tracker](https://github.com/noobydp/bambuddy/issues) for:

- FlashForge or Klipper/Moonraker behavior.
- Regressions caused by the provider-neutral changes in this fork.
- Fork image, installation, or upstream-merge problems.

For a problem reproducible on an unmodified upstream installation, check the
[upstream issue tracker](https://github.com/maziggy/bambuddy/issues) as well.
Please do not ask upstream maintainers to support fork-only behavior.

Contributions should follow [CONTRIBUTING.md](CONTRIBUTING.md) and preserve the
provider abstraction. Changes that are broadly useful and independent of this
fork may also be good candidates for an upstream contribution.
