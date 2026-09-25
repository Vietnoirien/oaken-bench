#!/usr/bin/env bash
# Lock / unlock named encrypted bundles: the held-out suite, and every later
# sealed oracle on the issue #26 ladder (reference engine, per-tier test
# suites, ...).
#
# WHY THIS EXISTS
#   Each bundle is only meaningful while its plaintext is absent from model
#   training corpora.  Plaintext files in a public git repo get crawled and
#   ingested, and the benchmark then decays SILENTLY -- scores drift upward
#   with no signal that the improvement is memorisation rather than
#   capability.
#
#   So every bundle ships as <name>.tar.gz.enc and gitignores <name>/.  The
#   passphrase is published right here in the clear: this is not a secret,
#   it is a speed bump.  The goal is that the plaintext never appears in a
#   crawl, not that humans cannot read it.  Anyone who wants the contents
#   runs `unlock <name>`.
#
#   Every bundle also carries its own canary (see CANARY.md).  If a model
#   can reproduce a bundle's canary, that bundle is in its training data and
#   its score is void.
#
#   This script used to be hard-coded to one bundle ("hidden", the held-out
#   suite).  It is now a small registry below -- add a line there when a new
#   sealed bundle ships.  A bare bundle name always means "hidden", so every
#   existing caller (bootstrap.sh, docker/scorer.sh's docs) keeps working
#   unchanged: `hidden.tar.gz.enc` and `hidden.sha256` are untouched files,
#   at the same paths, with the same format.
#
# USAGE
#   scripts/hidden.sh unlock [bundle]   # <bundle>.tar.gz.enc -> <bundle>/
#   scripts/hidden.sh lock   [bundle]   # <bundle>/ -> <bundle>.tar.gz.enc
#   scripts/hidden.sh status [bundle]   # what exists, and whether they agree
#   scripts/hidden.sh verify [bundle]   # decrypt to a temp dir and diff against <bundle>/
#   scripts/hidden.sh bundles           # list registered bundle names
#
#   bundle defaults to "hidden" -- the held-out suite.
#
#   Override a bundle's passphrase with OAKEN_<BUNDLE>_PASS=... (both ends
#   must match), e.g. OAKEN_HIDDEN_PASS for the "hidden" bundle. The bundle
#   name is upper-cased and '-' becomes '_' to build the variable name.
#
#   OAKEN_BENCH_ROOT overrides where bundles live (default: the repo root
#   this script sits in). Only ever used by tests -- pointing it at a temp
#   dir lets the machinery be exercised on throwaway bundles without going
#   near the real one.
set -euo pipefail

BENCH="${OAKEN_BENCH_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ITER=200000

# Registry of known bundles: name -> default passphrase. This is the only
# place a new sealed bundle needs to be added; dir, encrypted-file and
# digest-file names are all derived from the bundle name below, so every
# bundle gets the same lock/unlock/verify/status behaviour for free.
declare -A BUNDLE_DEFAULT_PASS=(
  [hidden]="oaken-bench-held-out"
  [refengine]="oaken-bench-refengine-v1"
  # One working bundle for whatever T3 (issue #42) instance is currently
  # generated locally (scripts/planted_bugs.py `generate --seal`). It holds
  # one instance at a time -- a per-instance manifest that "points at the
  # fix" (CANARY.md section 5) is meant to be regenerated and re-sealed per
  # seed, not accumulated into a growing multi-instance archive.
  [t3instance]="oaken-bench-t3instance-v1"
  [t5oracle]="oaken-bench-t5oracle-v1"
)

# Optional glob narrowing what counts as "the bundle's files" for the file
# count and canary grep in `status`. Bundles not listed here fall back to
# "every file", since not every bundle is made of *.test.ts (e.g. the
# reference engine is plain .ts).
declare -A BUNDLE_GLOB=(
  [hidden]="*.test.ts"
  [refengine]="*.ts"
  [t3instance]="*.ts"
  [t5oracle]="*.json"
)

die() { echo "error: $*" >&2; exit 1; }

bundle_names() {
  local n
  for n in "${!BUNDLE_DEFAULT_PASS[@]}"; do echo "$n"; done | sort
}

# Env var name for a bundle's passphrase override: OAKEN_<UPPER>_PASS.
passvar_name() {
  echo "OAKEN_$(echo "$1" | tr '[:lower:]-' '[:upper:]_')_PASS"
}

# Populates BUNDLE, DIR, ENC, DIGEST, GLOB, PASS as globals for $1 (default
# "hidden"). A single resolve step keeps every cmd_* function bundle-agnostic.
resolve_bundle() {
  BUNDLE="${1:-hidden}"
  [ -n "${BUNDLE_DEFAULT_PASS[$BUNDLE]+x}" ] \
    || die "unknown bundle '$BUNDLE' -- registered: $(bundle_names | tr '\n' ' ')"
  DIR="$BENCH/$BUNDLE"
  ENC="$BENCH/$BUNDLE.tar.gz.enc"
  DIGEST="$BENCH/$BUNDLE.sha256"
  GLOB="${BUNDLE_GLOB[$BUNDLE]:-*}"
  local var; var="$(passvar_name "$BUNDLE")"
  local override="${!var-}"
  PASS="${override:-${BUNDLE_DEFAULT_PASS[$BUNDLE]}}"
}

# Deterministic tar so an unchanged bundle produces an unchanged digest.
tar_plain() {
  tar -C "$BENCH" --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
      -cf - "$BUNDLE" | gzip -n
}

plain_digest() { tar_plain | sha256sum | cut -d' ' -f1; }

cmd_lock() {
  [ -d "$DIR" ] || die "no $DIR to lock"
  local n; n=$(find "$DIR" -name "$GLOB" | wc -l)
  [ "$n" -gt 0 ] || die "$DIR contains no $GLOB files -- refusing to lock an empty bundle"

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
  local n; n=$(find "$DIR" -name "$GLOB" | wc -l)
  echo "unlocked $n files -> $DIR"
}

cmd_verify() {
  [ -f "$ENC" ] || die "no $ENC"
  [ -d "$DIR" ] || die "no $DIR"
  local tmp; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITER" \
      -pass env:_OAKEN_PASS -in "$ENC" | tar -C "$tmp" -xzf -
  if diff -r "$DIR" "$tmp/$BUNDLE" >/dev/null; then
    echo "OK: $DIR is byte-identical to $(basename "$ENC")"
  else
    echo "DIFFER:"; diff -r "$DIR" "$tmp/$BUNDLE" || true; return 1
  fi
}

cmd_status() {
  echo "bundle     : $BUNDLE"
  echo "bench      : $BENCH"
  if [ -d "$DIR" ]; then
    echo "$BUNDLE/    : present, $(find "$DIR" -name "$GLOB" | wc -l) files ($GLOB)"
    # T3 keeps its source under src/, so the status check must search the
    # same recursive file set that the bundle count and lock command use.
    local canary canaried
    canary=$(find "$DIR" -name "$GLOB" -type f -exec grep -ho 'canary GUID [0-9a-f-]*' {} + | sort -u | head -1 || true)
    canaried=$(find "$DIR" -name "$GLOB" -type f -exec grep -l 'canary GUID' {} + | wc -l || true)
    echo "  canary   : ${canary:-MISSING}"
    echo "  canaried : $canaried/$(find "$DIR" -name "$GLOB" | wc -l) files"
  else
    echo "$BUNDLE/    : absent (run: scripts/hidden.sh unlock $BUNDLE)"
  fi
  [ -f "$ENC" ] && echo "encrypted  : $(basename "$ENC"), $(stat -c%s "$ENC") bytes" || echo "encrypted  : absent"
  [ -f "$DIGEST" ] && echo "recorded   : $(cat "$DIGEST")" || echo "recorded   : absent"
  if [ -d "$DIR" ] && [ -f "$DIGEST" ]; then
    [ "$(plain_digest)" = "$(cat "$DIGEST")" ] && echo "agreement  : $BUNDLE/ matches the blob" \
                                                || echo "agreement  : $BUNDLE/ has DRIFTED -- run 'lock'"
  fi
}

cmd_bundles() {
  bundle_names
}

VERB="${1:-status}"
case "$VERB" in
  bundles) cmd_bundles ;;
  lock|unlock|verify|status)
    resolve_bundle "${2:-hidden}"
    export _OAKEN_PASS="$PASS"
    case "$VERB" in
      lock)   cmd_lock ;;
      unlock) cmd_unlock ;;
      verify) cmd_verify ;;
      status) cmd_status ;;
    esac
    ;;
  *) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 64 ;;
esac
