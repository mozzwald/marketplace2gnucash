#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

find_python() {
  if [[ -n "${PYTHON:-}" ]] && command -v "${PYTHON}" >/dev/null 2>&1; then
    printf '%s\n' "${PYTHON}"
    return 0
  fi

  local candidate
  for candidate in python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

if ! PYTHON_BIN="$(find_python)"; then
  echo "No supported Python 3 interpreter found. Install Python 3.11+ and rerun." >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating virtual environment at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv --system-site-packages "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

if [[ -f "${VENV_DIR}/pyvenv.cfg" ]] && ! grep -qi '^include-system-site-packages = true$' "${VENV_DIR}/pyvenv.cfg"; then
  echo "Warning: existing ${VENV_DIR} may not include system site packages." >&2
  echo "If 'import gnucash' fails below, delete ${VENV_DIR} and rerun ./build.sh." >&2
fi

python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[build]"

if ! python -c "import gnucash" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
The selected build environment cannot import the GnuCash Python bindings.
Install the GnuCash bindings for this Python interpreter, or rerun with:

  PYTHON=/path/to/python ./build.sh

That interpreter must already succeed with:

  python -c "import gnucash"
EOF
  exit 1
fi

python tools/build_standalone.py --clean
