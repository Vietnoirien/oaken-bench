#!/usr/bin/env bash
# Lock / unlock the held-out test suite.
#
# WHY THIS EXISTS
#   The 132 held-out tests are only meaningful while they are absent from model
#   training corpora.  Plaintext .test.ts files in a public git repo get crawled
#   and ingested, and the benchmark then decays SILENTLY -- scores drift upward
#   with no signal that they are now partly memorisation.
#
#   So the repo ships `hidden.tar.gz.enc` and gitignores `hidden/`.  The
#   passphrase is published right here in the clear: this is not a secret, it is
#   a speed bump.  The goal is that the plaintext never appears in a crawl, not
#   that humans cannot read it.  Anyone who wants the tests runs `unlock`.
#
#   Every hidden file also carries a canary GUID (see CANARY.md).  If a model can
#   reproduce that GUID, the suite is in its training data and its score is void.
#
# USAGE
#   scripts/hidden.sh unlock    # hidden.tar.gz.enc -> hidden/
#   scripts/hidden.sh lock      # hidden/ -> hidden.tar.gz.enc
#   scripts/hidden.sh status    # what exists, and whether they agree
#   scripts/hidden.sh verify    # decrypt to a temp dir and diff against hidden/
#
#   Override the passphrase with OAKEN_HIDDEN_PASS=... (both ends must match).
set -euo pipefail

BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$BENCH/hidden"
ENC="$BENCH/hidden.tar.gz.enc"
DIGEST="$BENCH/hidden.sha256"
PASS="${OAKEN_HIDDEN_PASS:-oaken-bench-held-out}"
ITER=200000

die() { echo "error: $*" >&2; exit 1; }

# Deterministic tar so an unchanged suite produces an unchanged digest.
tar_plain() {
  tar -C "$BENCH" --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
      -cf - hidden | gzip -n
}

plain_digest() { tar_plain | sha256sum | cut -d' ' -f1; }

cmd_lock() {
  [ -d "$DIR" ] || die "no $DIR to lock"
  local n; n=$(find "$DIR" -name '*.test.ts' | wc -l)
  [ "$n" -gt 0 ] || die "$DIR contains no .test.ts files -- refusing to lock an empty suite"

  local d; d=$(plain_digest)
  if [ -f "$DIGEST" ] && [ "$(cat "$DIGEST")" = "$d" ] && [ -f "$ENC" ]; then
    echo "unchanged ($n files, $d) -- not re-encrypting"
    return 0
  fi
  tar_plain | openssl enc -aes-256-cbc -pbkdf2 -iter "$ITER" -salt \
      -pass env:_OAKEN_PASS -out "$ENC.tmp"
  mv "$ENC.tmp" "$ENC"
  echo "$d" > "$DIGEST"
  echo "locked $n files -> $(basename "$ENC") ($(stat -c%s "$ENC") bytes)"
  echo "plaintext sha256: $d"
}

cmd_unlock() {
  [ -f "$ENC" ] || die "no $ENC"
  if [ -d "$DIR" ]; then
    # Never clobber a modified working copy without saying so.
    if [ -f "$DIGEST" ] && [ "$(plain_digest)" = "$(cat "$DIGEST")" ]; then
      echo "$DIR already matches $(basename "$ENC") -- nothing to do"
      return 0
    fi
    die "$DIR exists and differs from $(basename "$ENC"). Move it aside, or run 'lock' to keep it."
  fi
  openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITER" \
      -pass env:_OAKEN_PASS -in "$ENC" | tar -C "$BENCH" -xzf -
  local n; n=$(find "$DIR" -name '*.test.ts' | wc -l)
  echo "unlocked $n files -> $DIR"
}

cmd_verify() {
  [ -f "$ENC" ] || die "no $ENC"
  [ -d "$DIR" ] || die "no $DIR"
  local tmp; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITER" \
      -pass env:_OAKEN_PASS -in "$ENC" | tar -C "$tmp" -xzf -
  if diff -r "$DIR" "$tmp/hidden" >/dev/null; then
    echo "OK: $DIR is byte-identical to $(basename "$ENC")"
  else
    echo "DIFFER:"; diff -r "$DIR" "$tmp/hidden" || true; return 1
  fi
}

cmd_status() {
  echo "bench      : $BENCH"
  if [ -d "$DIR" ]; then
    echo "hidden/    : present, $(find "$DIR" -name '*.test.ts' | wc -l) test files"
    echo "  canary   : $(grep -ho 'canary GUID [0-9a-f-]*' "$DIR"/*.test.ts 2>/dev/null | sort -u | head -1 || echo 'MISSING')"
    echo "  canaried : $(grep -lc 'canary GUID' "$DIR"/*.test.ts 2>/dev/null | wc -l)/$(find "$DIR" -name '*.test.ts' | wc -l) files"
  else
    echo "hidden/    : absent (run: scripts/hidden.sh unlock)"
  fi
  [ -f "$ENC" ] && echo "encrypted  : $(basename "$ENC"), $(stat -c%s "$ENC") bytes" || echo "encrypted  : absent"
  [ -f "$DIGEST" ] && echo "recorded   : $(cat "$DIGEST")" || echo "recorded   : absent"
  if [ -d "$DIR" ] && [ -f "$DIGEST" ]; then
    [ "$(plain_digest)" = "$(cat "$DIGEST")" ] && echo "agreement  : hidden/ matches the blob" \
                                                || echo "agreement  : hidden/ has DRIFTED -- run 'lock'"
  fi
}

export _OAKEN_PASS="$PASS"
case "${1:-status}" in
  lock)   cmd_lock ;;
  unlock) cmd_unlock ;;
  verify) cmd_verify ;;
  status) cmd_status ;;
  *) sed -n '2,28p' "${BASH_SOURCE[0]}"; exit 64 ;;
esac
