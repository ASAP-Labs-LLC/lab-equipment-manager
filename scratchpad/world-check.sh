#!/bin/zsh
# Which world modules have changed since the baseline, and is the change real?
#
# `engine.js` had its mtime moved three times on 2026-08-08 with no agent owning
# it and no route-patch signature on disk. mtime alone could not tell us whether
# the CONTENT changed. A hash can. Re-baseline deliberately after a round lands:
#     ./world-check.sh --rebase
cd "$HOME/LAB-lem/LEM Web Server/static/world" || exit 1
BASE="$HOME/LAB-lem/scratchpad/world-baseline.md5"
if [[ "${1:-}" == "--rebase" ]]; then
  md5 -r *.js > "$BASE" 2>/dev/null || md5sum *.js > "$BASE"
  echo "re-baselined $(wc -l < "$BASE" | tr -d ' ') modules"; exit 0
fi
tmp=$(mktemp); md5 -r *.js > "$tmp" 2>/dev/null || md5sum *.js > "$tmp"
if diff -q "$BASE" "$tmp" >/dev/null; then
  echo "all modules byte-identical to baseline"
else
  echo "CHANGED since baseline:"; diff "$BASE" "$tmp" | command grep '^>' | awk '{print "  "$NF}'
fi
rm -f "$tmp"
