#!/bin/sh
# install.sh -- install the secret-scan pre-commit hook into a git repo.
#
#   scripts/git-hooks/install.sh [REPO]          # default: the repo holding this script
#   scripts/git-hooks/install.sh --force [REPO]  # replace a foreign pre-commit (backed up)
#   scripts/git-hooks/install.sh --uninstall [REPO]
#
# .git/ is not Syncthing-synced, so run this once per machine (ix, Straylight).
# The hook is a 6-line wrapper that execs secret_scan.py from THIS checkout, so
# rule updates land with a normal pull -- no reinstall.
set -eu
FORCE=0; UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) break ;;
  esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"
SCANNER="$HERE/secret_scan.py"
REPO="${1:-$(git -C "$HERE" rev-parse --show-toplevel)}"
HOOKS="$(cd "$REPO" && git rev-parse --path-format=absolute --git-path hooks)"
DEST="$HOOKS/pre-commit"
MARK="# secret-scan hook (i446-monorepo/scripts/git-hooks)"

if [ "$UNINSTALL" = 1 ]; then
  if [ -f "$DEST" ] && grep -qF "$MARK" "$DEST"; then rm -f "$DEST"; echo "removed $DEST"; else echo "no secret-scan hook at $DEST"; fi
  exit 0
fi
mkdir -p "$HOOKS"
if [ -e "$DEST" ] && ! grep -qF "$MARK" "$DEST"; then
  if [ "$FORCE" != 1 ]; then
    echo "refusing: $DEST exists and is not ours (rerun with --force to back it up and replace)" >&2
    exit 1
  fi
  mv "$DEST" "$DEST.bak.$(date +%Y%m%d%H%M%S)"
fi
cat > "$DEST" <<HOOK
#!/bin/sh
$MARK
# Fail open with a warning if the scanner moved, so autopush never bricks.
S="$SCANNER"
[ -f "\$S" ] || { echo "secret-scan: \$S missing, commit NOT scanned" >&2; exit 0; }
exec python3 -I "\$S"
HOOK
chmod +x "$DEST"
echo "installed $DEST -> $SCANNER"
