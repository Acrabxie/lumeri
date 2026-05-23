#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hooks_dir="$repo_root/.git/hooks"

if [[ ! -d "$hooks_dir" ]]; then
  echo "Git hooks directory not found: $hooks_dir" >&2
  exit 2
fi

cat > "$hooks_dir/post-commit" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
log_path="/tmp/gemia_post_commit_backup.log"

if ! "$repo_root/scripts/backup_gemia_version.sh" --label post-commit >"$log_path" 2>&1; then
  echo "Gemia external backup failed. Commit version was created, but backup did not complete." >&2
  echo "Fix the backup disk or run scripts/backup_gemia_version.sh manually." >&2
  cat "$log_path" >&2 || true
  exit 1
fi

echo "Gemia backup: $(cat "$log_path")"
HOOK

cat > "$hooks_dir/post-merge" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
log_path="/tmp/gemia_post_merge_backup.log"

if ! "$repo_root/scripts/backup_gemia_version.sh" --label post-merge >"$log_path" 2>&1; then
  echo "Gemia external backup failed after merge. Run scripts/backup_gemia_version.sh manually." >&2
  cat "$log_path" >&2 || true
  exit 0
fi

echo "Gemia backup: $(cat "$log_path")"
HOOK

chmod +x "$hooks_dir/post-commit" "$hooks_dir/post-merge"
echo "Installed Gemia backup hooks in $hooks_dir"
