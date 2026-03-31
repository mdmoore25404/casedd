---
title: CLI Reference
nav_order: 5
permalink: /cli/
---

# `casedd-ctl` Reference

`casedd-ctl` is the command-line wrapper for the CASEDD HTTP API.

The command is available in two forms:

- installed console script: `casedd-ctl`
- repository-local wrapper: `./casedd-ctl`

## Common usage

```bash
./casedd-ctl status
./casedd-ctl health
./casedd-ctl templates list
./casedd-ctl templates set htop
./casedd-ctl metrics
./casedd-ctl snapshot --output /tmp/casedd.jpg
./casedd-ctl data --prefix cpu
./casedd-ctl reload --pid-file run/casedd-dev.pid
./casedd-ctl --json health
```

## Help subcommand

The CLI includes a `help` subcommand for nested command help.

```bash
./casedd-ctl help
./casedd-ctl help templates
./casedd-ctl help templates set
```

## Global options

- `--url`: target a non-default daemon base URL
- `--json`: print JSON instead of formatted text

Example:

```bash
./casedd-ctl --url http://localhost:18080 --json health
```

## Commands

### `status`

Shows overall daemon status, uptime, and the currently active template for each panel.

### `health`

Shows overall daemon health plus per-getter state and error counts.

### `templates list`

Lists available templates.

### `templates set <name>`

Sets the active template override for the default panel or a named panel.

### `metrics`

Prints the raw Prometheus metrics text from `/api/metrics`.

### `snapshot`

Saves the current rendered frame as a JPEG file.

### `data`

Prints the data store snapshot, optionally filtered by `--prefix`.

### `reload`

Sends `SIGHUP` to the daemon PID from the configured PID file.