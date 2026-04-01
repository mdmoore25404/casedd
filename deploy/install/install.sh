#!/usr/bin/env bash
# install.sh — install or remove CASEDD as a systemd service in-place from the current clone.

set -euo pipefail

SERVICE_NAME="casedd"
ENV_DIR="/etc/casedd"
ENV_FILE="${ENV_DIR}/casedd.env"
LOG_DIR="/var/log/casedd"
UNIT_TEMPLATE="deploy/casedd.service"
UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
DRY_RUN=0
PURGE_ENV=0
PURGE_LOGS=0
REMOVE_VENV=0

usage() {
    cat <<'EOF'
Usage:
  sudo ./deploy/install/install.sh [install]
  sudo ./deploy/install/install.sh uninstall [--purge-env] [--purge-logs] [--remove-venv]
  sudo ./deploy/install/install.sh status

Options:
  --dry-run     Print what would change without modifying the system.
  --purge-env   Remove /etc/casedd/casedd.env during uninstall.
  --purge-logs  Remove /var/log/casedd during uninstall.
  --remove-venv Remove the repo-local .venv during uninstall.

Notes:
  - The service runs directly from the clone this script is executed from.
  - Moving the repository later requires rerunning install.
  - Single-user installs adopt the repo-local .env by symlinking
    /etc/casedd/casedd.env -> .env. If a stale plain env file already exists,
    it is backed up and replaced with the symlink.
  - casedd.yaml is read directly from the repository working tree via the
    service WorkingDirectory; it is not copied into /etc/casedd.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

log() {
    echo "==> $*"
}

run_cmd() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "DRY-RUN: $*"
        return 0
    fi
    "$@"
}

run_shell() {
    local command="$1"

    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "DRY-RUN: ${command}"
        return 0
    fi
    eval "${command}"
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || fail "This script must be run as root via sudo."
}

detect_service_user() {
    if [[ -n "${CASEDD_SERVICE_USER:-}" ]]; then
        printf '%s\n' "${CASEDD_SERVICE_USER}"
        return
    fi

    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        printf '%s\n' "${SUDO_USER}"
        return
    fi

    local repo_owner
    repo_owner="$(stat -c '%U' "${REPO_ROOT}")"
    if [[ "${repo_owner}" != "root" ]]; then
        printf '%s\n' "${repo_owner}"
        return
    fi

    printf '%s\n' "casedd"
}

ensure_service_user() {
    local service_user="$1"

    if id -u "${service_user}" >/dev/null 2>&1; then
        return
    fi

    if [[ "${service_user}" == "casedd" ]]; then
        log "Creating fallback system user '${service_user}'"
        run_cmd useradd --system --home-dir "${REPO_ROOT}" --shell /usr/sbin/nologin \
            "${service_user}"
        return
    fi

    fail "Selected service user '${service_user}' does not exist. Set CASEDD_SERVICE_USER explicitly."
}

validate_repo() {
    [[ -f "${REPO_ROOT}/pyproject.toml" ]] || fail "Repository root looks wrong: ${REPO_ROOT}"
    [[ -f "${REPO_ROOT}/${UNIT_TEMPLATE}" ]] || fail "Missing unit template: ${UNIT_TEMPLATE}"
    [[ -f "${REPO_ROOT}/requirements.txt" ]] || fail "Missing requirements.txt in ${REPO_ROOT}"
}

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

render_unit() {
    local service_user="$1"
    local rendered_path="$2"
    local escaped_root
    local escaped_user

    escaped_root="$(escape_sed "${REPO_ROOT}")"
    escaped_user="$(escape_sed "${service_user}")"

    sed \
        -e "s|@CASEDD_REPO_ROOT@|${escaped_root}|g" \
        -e "s|@CASEDD_USER@|${escaped_user}|g" \
        "${REPO_ROOT}/${UNIT_TEMPLATE}" > "${rendered_path}"
}

install_env_file() {
    run_cmd mkdir -p "${ENV_DIR}"

    local repo_env="${REPO_ROOT}/.env"
    local repo_owner=""

    if [[ -f "${repo_env}" && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        repo_owner="$(stat -c '%U' "${REPO_ROOT}")"
    fi

    # Already a symlink — nothing to do.
    if [[ -L "${ENV_FILE}" ]]; then
        log "Preserving existing symlink ${ENV_FILE} -> $(readlink "${ENV_FILE}")"
        return
    fi

    # Single-user install: if the repo has a populated .env and the invoking
    # user owns the repo, create a symlink so edits to .env are picked up on
    # the next service restart without re-running the installer.
    if [[ -f "${repo_env}" && "${repo_owner}" == "${SUDO_USER:-}" ]]; then
        if [[ -f "${ENV_FILE}" ]]; then
            local backup_path="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
            log "Single-user install — backing up ${ENV_FILE} -> ${backup_path}"
            if [[ "${DRY_RUN}" -eq 1 ]]; then
                echo "DRY-RUN: mv ${ENV_FILE} ${backup_path}"
            else
                run_cmd mv "${ENV_FILE}" "${backup_path}"
            fi
        fi

        log "Single-user install — symlinking ${ENV_FILE} -> ${repo_env}"
        if [[ "${DRY_RUN}" -eq 1 ]]; then
            echo "DRY-RUN: ln -sf ${repo_env} ${ENV_FILE}"
        else
            run_cmd ln -sf "${repo_env}" "${ENV_FILE}"
        fi
        return
    fi

    # Already a plain file (previous install or manual setup) — preserve it.
    if [[ -f "${ENV_FILE}" ]]; then
        log "Preserving existing environment file ${ENV_FILE}"
        return
    fi

    # Multi-user / system install: copy the example template.
    log "Installing environment template to ${ENV_FILE}"
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "DRY-RUN: install -m 0640 ${REPO_ROOT}/.env.example ${ENV_FILE}"
        return
    fi
    install -m 0640 "${REPO_ROOT}/.env.example" "${ENV_FILE}"
}

install_venv() {
    log "Creating or updating virtual environment in ${VENV_DIR}"
    run_cmd python3.12 -m venv "${VENV_DIR}"
    run_cmd "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    run_cmd "${VENV_DIR}/bin/pip" install --quiet -r "${REPO_ROOT}/requirements.txt"
}

install_unit() {
    local service_user="$1"
    local rendered_path

    rendered_path="$(mktemp)"
    render_unit "${service_user}" "${rendered_path}"

    log "Installing systemd unit for user '${service_user}' from ${REPO_ROOT}"
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "DRY-RUN: install -m 0644 ${rendered_path} ${UNIT_DEST}"
    else
        install -m 0644 "${rendered_path}" "${UNIT_DEST}"
    fi

    run_cmd systemctl daemon-reload
    run_cmd systemctl enable --now "${SERVICE_NAME}.service"
    rm -f "${rendered_path}"
}

install_logs_dir() {
    run_cmd mkdir -p "${LOG_DIR}"
}

install_service() {
    local service_user

    service_user="$(detect_service_user)"
    validate_repo
    ensure_service_user "${service_user}"

    log "Installing CASEDD from ${REPO_ROOT}"
    log "Service user: ${service_user}"
    install_venv
    install_env_file
    install_logs_dir
    install_unit "${service_user}"

    echo
    echo "Installation complete."
    echo "  Repo path: ${REPO_ROOT}"
    echo "  Service: ${SERVICE_NAME}.service"
    if [[ -L "${ENV_FILE}" ]]; then
        echo "  Env file: ${ENV_FILE} -> $(readlink "${ENV_FILE}") (symlink)"
    else
        echo "  Env file: ${ENV_FILE}"
    fi
    echo
    echo "Useful commands:"
    echo "  sudo systemctl status ${SERVICE_NAME}"
    echo "  sudo journalctl -u ${SERVICE_NAME} -n 100"
    if [[ -L "${ENV_FILE}" ]]; then
        echo "  Edit env:  \$EDITOR ${REPO_ROOT}/.env  (symlinked — no sudo needed)"
    else
        echo "  sudoedit ${ENV_FILE}"
    fi
    echo
    echo "If you move this clone, rerun: sudo ./deploy/install/install.sh"
}

uninstall_service() {
    log "Removing CASEDD systemd service"

    if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
        run_cmd systemctl disable --now "${SERVICE_NAME}.service"
    fi

    if [[ -f "${UNIT_DEST}" ]]; then
        run_cmd rm -f "${UNIT_DEST}"
    fi
    run_cmd systemctl daemon-reload

    if [[ "${PURGE_ENV}" -eq 1 && -f "${ENV_FILE}" ]]; then
        run_cmd rm -f "${ENV_FILE}"
    fi

    if [[ "${PURGE_LOGS}" -eq 1 && -d "${LOG_DIR}" ]]; then
        run_cmd rm -rf "${LOG_DIR}"
    fi

    if [[ "${REMOVE_VENV}" -eq 1 && -d "${VENV_DIR}" ]]; then
        run_cmd rm -rf "${VENV_DIR}"
    fi

    echo
    echo "Uninstall complete."
    if [[ "${PURGE_ENV}" -eq 0 ]]; then
        echo "  Preserved env file: ${ENV_FILE}"
    fi
    if [[ "${PURGE_LOGS}" -eq 0 ]]; then
        echo "  Preserved logs dir: ${LOG_DIR}"
    fi
    if [[ "${REMOVE_VENV}" -eq 0 ]]; then
        echo "  Preserved repo virtualenv: ${VENV_DIR}"
    fi
}

show_status() {
    systemctl status "${SERVICE_NAME}.service" --no-pager || true
}

ACTION="install"
while [[ $# -gt 0 ]]; do
    case "$1" in
        install|uninstall|status)
            ACTION="$1"
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --purge-env)
            PURGE_ENV=1
            ;;
        --purge-logs)
            PURGE_LOGS=1
            ;;
        --remove-venv)
            REMOVE_VENV=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
    shift
done

require_root

case "${ACTION}" in
    install)
        install_service
        ;;
    uninstall)
        uninstall_service
        ;;
    status)
        show_status
        ;;
esac
