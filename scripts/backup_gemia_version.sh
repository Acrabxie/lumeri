#!/usr/bin/env bash
set -euo pipefail

repo_root="${GEMIA_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ -n "${GEMIA_BACKUP_ROOT:-}" ]]; then
  backup_root="$GEMIA_BACKUP_ROOT"
elif [[ -d "/Volumes/Extreme SSD" ]]; then
  backup_root="/Volumes/Extreme SSD/GemiaBackups"
else
  backup_root="/Volumes/ExtremeSSD/GemiaBackups"
fi
label="manual"

if [[ "${1:-}" == "--label" ]]; then
  label="${2:-manual}"
elif [[ "${1:-}" != "" ]]; then
  label="$1"
fi

if [[ "$backup_root" == /Volumes/* ]]; then
  volume="/Volumes/$(printf '%s' "${backup_root#/Volumes/}" | cut -d/ -f1)"
  if [[ ! -d "$volume" ]]; then
    echo "Backup volume is not mounted: $volume" >&2
    exit 2
  fi
fi

timestamp="$(date '+%Y%m%d-%H%M%S')"
iso_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
git_rev="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || printf 'nogit')"
safe_label="$(printf '%s' "$label" | tr -cs '[:alnum:]_.-' '-' | sed 's/^-//; s/-$//')"
if [[ -z "$safe_label" ]]; then
  safe_label="manual"
fi

dirty="false"
if ! git -C "$repo_root" diff --quiet --ignore-submodules -- 2>/dev/null \
  || ! git -C "$repo_root" diff --cached --quiet --ignore-submodules -- 2>/dev/null \
  || [[ -n "$(git -C "$repo_root" ls-files --others --exclude-standard 2>/dev/null)" ]]; then
  dirty="true"
fi

dirty_suffix=""
if [[ "$dirty" == "true" ]]; then
  dirty_suffix="-dirty"
fi

versions_dir="$backup_root/versions"
mkdir -p "$versions_dir"

snapshot_name="${timestamp}-${git_rev}${dirty_suffix}-${safe_label}"
snapshot="$versions_dir/$snapshot_name"
counter=1
while [[ -e "$snapshot" ]]; do
  snapshot="$versions_dir/${snapshot_name}-${counter}"
  counter=$((counter + 1))
done

latest_link="$backup_root/latest"

tar_exclude_args=(
  --exclude ".git"
  --exclude ".DS_Store"
  --exclude ".pytest_cache"
  --exclude ".venv"
  --exclude "__pycache__"
  --exclude "build"
  --exclude "tauri-app/node_modules"
  --exclude "tauri-app/src-tauri/target"
  --exclude "htmlcov"
  --exclude "*.pyc"
  --exclude "*.log"
)

if [[ "${GEMIA_BACKUP_INCLUDE_RUNTIME:-0}" != "1" ]]; then
  tar_exclude_args+=(
    --exclude "demo"
    --exclude "frames"
    --exclude "inputs"
    --exclude "logs"
    --exclude "notes"
    --exclude "outputs"
    --exclude "plans"
    --exclude "styled"
    --exclude "tasks"
    --exclude "temp"
    --exclude "timeline"
  )
fi

mkdir -p "$snapshot"
tar -czf "$snapshot/gemia-source.tar.gz" -C "$repo_root" "${tar_exclude_args[@]}" .
git -C "$repo_root" bundle create "$snapshot/gemia.git.bundle" --all >/dev/null 2>&1 || true

git -C "$repo_root" status --short > "$snapshot/GEMIA_BACKUP_STATUS.txt" 2>/dev/null || true
git -C "$repo_root" diff --binary > "$snapshot/GEMIA_BACKUP_DIFF.patch" 2>/dev/null || true
git -C "$repo_root" diff --cached --binary >> "$snapshot/GEMIA_BACKUP_DIFF.patch" 2>/dev/null || true

{
  printf 'timestamp_utc=%s\n' "$iso_utc"
  printf 'repo_root=%s\n' "$repo_root"
  printf 'backup_root=%s\n' "$backup_root"
  printf 'snapshot=%s\n' "$snapshot"
  printf 'git_rev=%s\n' "$git_rev"
  printf 'dirty=%s\n' "$dirty"
  printf 'label=%s\n' "$label"
  printf 'include_runtime=%s\n' "${GEMIA_BACKUP_INCLUDE_RUNTIME:-0}"
} > "$snapshot/GEMIA_BACKUP_MANIFEST.txt"

ln -sfn "$snapshot" "$latest_link"

changelog="$backup_root/CHANGELOG.md"
{
  printf '\n## %s - %s\n\n' "$iso_utc" "$(basename "$snapshot")"
  printf -- '- repo: `%s`\n' "$repo_root"
  printf -- '- git: `%s`\n' "$git_rev"
  printf -- '- dirty: `%s`\n' "$dirty"
  printf -- '- label: `%s`\n' "$label"
  printf -- '- snapshot: `%s`\n' "$snapshot"
  printf -- '- changed files:\n'
  if git -C "$repo_root" status --short >/tmp/gemia_backup_status.$$ 2>/dev/null; then
    if [[ -s /tmp/gemia_backup_status.$$ ]]; then
      sed 's/^/  - `/' /tmp/gemia_backup_status.$$ | sed 's/$/`/'
    else
      printf '  - `clean`\n'
    fi
    rm -f /tmp/gemia_backup_status.$$
  else
    printf '  - `git status unavailable`\n'
  fi
} >> "$changelog"

printf '%s\n' "$snapshot"
