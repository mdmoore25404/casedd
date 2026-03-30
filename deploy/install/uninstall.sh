#!/usr/bin/env bash
# uninstall.sh — convenience wrapper for removing the CASEDD systemd service.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "${SCRIPT_DIR}/install.sh" uninstall "$@"