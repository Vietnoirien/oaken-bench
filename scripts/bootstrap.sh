#!/usr/bin/env bash
# One-time setup. Vendors dependencies and builds the runner image.
#
# Runs are executed with the network OFF, so every dependency must be present
# in the image before the first trial starts. That is what this script does.
set -euo pipefail
B="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> installing seed dependencies (pinned by package-lock.json)"
( cd "$B/seed" && npm ci --no-audit --no-fund )

echo "==> syncing seed into the docker build context"
rm -rf "$B/docker/seed"
cp -a "$B/seed" "$B/docker/seed"

echo "==> unlocking the held-out suite"
"$B/scripts/hidden.sh" unlock || echo "    (already unlocked)"

echo "==> building image oaken-bench:1.0"
# NOTE: the build context is docker/, not the repo root. docker/ carries its own
# copy of seed/ so the image can never pick up a mutated working tree.
docker build -t oaken-bench:1.0 "$B/docker/"

echo
echo "done. Next: register a model (see MODELS.md), start llama-server on the"
echo "docker bridge, then:  ./run.sh pi <model-id> <label>"
