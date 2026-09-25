#!/bin/bash
# Score a finished run INSIDE the container.
#   entrypoint.sh score [--detail]
#
# Mounts expected:
#   /in    ro  the result directory (workspace.tgz)
#   /enc   ro  directory holding hidden.tar.gz.enc
#   /out   rw  where the suite JSON is written
# Env:
#   OAKEN_HIDDEN_PASS   passphrase for the held-out suite
#
# WHY IN A CONTAINER
#   1. The held-out suite is decrypted to a tmpfs path that only exists inside
#      this container. Plaintext never touches the host filesystem, so it cannot
#      be picked up by a local indexer, committed by accident, or swept into a
#      dataset. /out is host-mounted and NEVER receives test source.
#   2. Agent-generated code can fail to terminate. vitest forks a worker pool,
#      and killing the parent on the host left ~85 orphans pinning four cores for
#      seven hours. `docker run --rm` reaps the entire tree by construction.
#   3. node and vitest are pinned by the image, so scores do not drift with the
#      host toolchain.
set -u
DETAIL=0
[ "${1:-}" = "--detail" ] && DETAIL=1

BOUND=420
HID=/tmp/hidden          # container-only, never mounted
OUT=/out

restore() {
  rm -rf /work && mkdir -p /work
  tar xzf /in/workspace.tgz -C /tmp
  cp -a /tmp/work/. /work/
  # node_modules and .git were excluded from the archive.
  cp -a /opt/seed/node_modules /work/node_modules
}

run_suite() {   # $1 = tag
  local tag="$1"
  timeout -s KILL "$BOUND" \
    npx vitest run --reporter=json --testTimeout=10000 \
        --outputFile="/tmp/vitest-$tag.json" >/dev/null 2>&1
  local rc=$?
  if [ ! -s "/tmp/vitest-$tag.json" ]; then
    echo "{\"__hung\": $([ $rc -eq 137 ] && echo true || echo false)}" > "$OUT/suite-$tag.json"
    return
  fi
  # Counts only by default. When detail is on, per-test entries are added
  # too (see score_detail.py) -- but the held-out test NAMES are
  # themselves benchmark data: emitting them to a host mount would defeat
  # the encryption, so the hidden suite's per-test entries are
  # content-addressed digests, never names. Only the visible suite (public
  # already) gets plaintext names.
  python3 /usr/local/bin/score_detail.py "$tag" "$DETAIL" \
      "/tmp/vitest-$tag.json" "$OUT/suite-$tag.json"
}

cd /work 2>/dev/null || true
restore
cd /work

# --- visible suite ----------------------------------------------------------
# Against the seed's ORIGINAL tests: an agent that edits or deletes a visible
# test must not thereby change its own score.
rm -rf /work/tests && cp -a /opt/seed/tests /work/tests
run_suite visible

# --- typecheck, on the agent's code with the ORIGINAL visible tests ----------
if timeout -s KILL 600 npx tsc --noEmit >/dev/null 2>&1; then
  echo '{"clean":true}'  > "$OUT/typecheck.json"
else
  echo '{"clean":false}' > "$OUT/typecheck.json"
fi

# --- held-out suite ---------------------------------------------------------
mkdir -p "$HID"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass env:OAKEN_HIDDEN_PASS -in /enc/hidden.tar.gz.enc 2>/dev/null \
  | tar -C "$HID" -xzf - 2>/dev/null
if [ ! -d "$HID/hidden" ] || [ -z "$(ls -A "$HID/hidden" 2>/dev/null)" ]; then
  echo '{"__error":"could not decrypt held-out suite"}' > "$OUT/suite-hidden.json"
else
  rm -rf /work/tests && mkdir -p /work/tests
  cp "$HID"/hidden/*.test.ts /work/tests/
  run_suite hidden
  rm -rf "$HID" /work/tests           # belt and braces; the container dies anyway
fi

# --- frozen-artefact check, against the image's pristine seed ----------------
python3 - <<'PY'
import hashlib,json,os
FROZEN=['SPEC.md','data/items.json','package.json','tsconfig.json','vitest.config.ts']
t=[]
for rel in FROZEN:
    a,b='/opt/seed/'+rel,'/work/'+rel
    if not os.path.exists(b): t.append(rel+' (deleted)'); continue
    h=lambda p: hashlib.sha256(open(p,'rb').read()).hexdigest()
    if h(a)!=h(b): t.append(rel)
json.dump(t,open('/out/tampered.json','w'))
PY

[ -n "${HOST_UID:-}" ] && chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$OUT" 2>/dev/null
echo "scored"
