#!/usr/bin/env bash
# link.sh: recreate every dotfile link listed in links.conf.
#   ./link.sh            check + fix your own links (kind "link")
#   ./link.sh -n         dry run: only show what would change
#   ./link.sh --root     also do the root entries (rootlink / rootcopy), via sudo
# Safe to re-run: a correct link is left alone; a real file in the way is kept as <target>.pre-link-<date>.
set -uo pipefail

REPO=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
MANIFEST="$REPO/links.conf"
STAMP=$(date +%Y-%m-%d)
DRY=0 ROOT=0
for a in "$@"; do
  case "$a" in
    -n|--dry-run) DRY=1 ;;
    --root)       ROOT=1 ;;
    -h|--help)    sed -n '2,6p' "$0"; exit 0 ;;
    *)            echo "link: unknown option $a" >&2; exit 2 ;;
  esac
done

ok=0 changed=0 skipped=0 failed=0
say() { printf '%-8s %s\n' "$1" "$2"; }
run() { (( DRY )) && return 0; "$@"; }

do_link() {   # $1 sudo-prefix ("" or "sudo"), $2 src, $3 dest
  local S=$1 src=$2 dest=$3
  if [[ -L $dest && $(readlink -f "$dest") == "$(readlink -f "$src")" ]]; then
    ok=$((ok+1)); return
  fi
  if [[ -e $dest || -L $dest ]] && [[ ! -L $dest ]]; then
    say backup "$dest -> $dest.pre-link-$STAMP"
    run $S mv "$dest" "$dest.pre-link-$STAMP" || { failed=$((failed+1)); return; }
  elif [[ -L $dest ]]; then
    say relink "$dest (was -> $(readlink "$dest"))"
    run $S rm "$dest" || { failed=$((failed+1)); return; }
  fi
  say link "$dest -> $src"
  run $S mkdir -p "$(dirname "$dest")" && run $S ln -s "$src" "$dest" && changed=$((changed+1)) || failed=$((failed+1))
}

do_copy() {   # $1 src, $2 dest, $3 mode
  local src=$1 dest=$2 mode=${3:-644}
  if sudo -n true 2>/dev/null || (( ! DRY )); then
    if sudo cmp -s "$src" "$dest" 2>/dev/null; then ok=$((ok+1)); return; fi
  elif cmp -s "$src" "$dest" 2>/dev/null; then
    ok=$((ok+1)); return
  fi
  say install "$dest ($mode)"
  run sudo install -D -m "$mode" -o root -g root "$src" "$dest" && changed=$((changed+1)) || failed=$((failed+1))
  [[ $dest == /etc/systemd/system/* ]] && NEED_RELOAD=1
}

NEED_RELOAD=0
while read -r kind rel dest mode; do
  [[ -z ${kind:-} || $kind == \#* ]] && continue
  src="$REPO/$rel"
  dest=${dest/#\~/$HOME}
  if [[ ! -e $src ]]; then say missing "$src"; failed=$((failed+1)); continue; fi
  case "$kind" in
    link)     do_link "" "$src" "$dest" ;;
    rootlink) if (( ROOT )); then do_link sudo "$src" "$dest"; else skipped=$((skipped+1)); fi ;;
    rootcopy) if (( ROOT )); then do_copy "$src" "$dest" "$mode"; else skipped=$((skipped+1)); fi ;;
    *)        say unknown "$kind in $MANIFEST"; failed=$((failed+1)) ;;
  esac
done < "$MANIFEST"

(( NEED_RELOAD && ! DRY )) && sudo systemctl daemon-reload

echo "link: $ok ok, $changed changed, $failed failed$( (( skipped )) && echo ", $skipped root entries skipped (use --root)")$( (( DRY )) && echo " [dry run]")"
(( failed == 0 ))
