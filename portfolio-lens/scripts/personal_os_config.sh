# scripts/personal_os_config.sh
# Source this, then call `personal_os_dir exchange_dir` or `personal_os_dir scratch_dir`
_POS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
personal_os_dir() {
  python3 "${_POS_DIR}/personal_os_config.py" --get "$1"
}
