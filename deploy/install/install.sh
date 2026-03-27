#!/usr/bin/env bash
# install.sh — Install CASEDD as a systemd service
#
# Usage: sudo ./deploy/install/install.sh
#
# What this does:
#   1. Creates a dedicated 'casedd' system user in the 'video' group.
#   2. Copies the project to /opt/casedd.
#   3. Creates a Python virtual environment and installs dependencies.
#   4. Installs the environment file template to /etc/casedd/.
#   5. Installs and enables the systemd service unit.
#
# Requirements: Python 3.12, pip, git.

set -euo pipefail

INSTALL_DIR="/opt/casedd"
SERVICE_USER="casedd"
ENV_DIR="/etc/casedd"
LOG_DIR="/var/log/casedd"
SERVICE_NAME="casedd"
UNIT_FILE="deploy/casedd.service"
UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Must be run as root
if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

echo "==> Installing CASEDD to ${INSTALL_DIR}"

# Create system user
if ! id -u "${SERVICE_USER}" &>/dev/null; then
    useradd --system --groups video --home-dir "${INSTALL_DIR}" \
        --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    echo "    Created system user '${SERVICE_USER}'."
else
    echo "    User '${SERVICE_USER}' already exists."
fi

# Copy project files
mkdir -p "${INSTALL_DIR}"
rsync -a --exclude='.git' --exclude='.venv' --exclude='run/' --exclude='logs/' \
    "${REPO_ROOT}/" "${INSTALL_DIR}/"
echo "    Project files copied."

# Create virtual environment
python3.12 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
echo "    Virtual environment created."

# Environment file
mkdir -p "${ENV_DIR}"
if [[ ! -f "${ENV_DIR}/casedd.env" ]]; then
    cp "${INSTALL_DIR}/.env.example" "${ENV_DIR}/casedd.env"
    chmod 640 "${ENV_DIR}/casedd.env"
    chown root:casedd "${ENV_DIR}/casedd.env"
    echo "    Environment file installed to ${ENV_DIR}/casedd.env — edit before starting."
else
    echo "    Environment file already exists at ${ENV_DIR}/casedd.env — not overwriting."
fi

# Log directory
mkdir -p "${LOG_DIR}"
chown "${SERVICE_USER}:${SERVICE_USER}" "${LOG_DIR}"
echo "    Log directory: ${LOG_DIR}"

# Fix ownership
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# Systemd unit
cp "${INSTALL_DIR}/${UNIT_FILE}" "${UNIT_DEST}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
echo "    Systemd service installed and enabled."

echo ""
echo "Installation complete."
echo ""
echo "Next steps:"
echo "  1. Edit ${ENV_DIR}/casedd.env to set CASEDD_TEMPLATE, ports, etc."
echo "  2. sudo systemctl start casedd"
echo "  3. sudo journalctl -fu casedd"
