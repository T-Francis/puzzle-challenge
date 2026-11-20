#!/bin/sh
# Runs the processing pipeline in its container, at the repository root:
#   sh scripts/pipeline.sh all          (see src/pipeline.py for the commands)
# Any other script: sh scripts/pipeline.sh --script src/montage.py A001,B018 out.jpg
set -e
cd "$(dirname "$0")/.."
docker build -q --target pipeline -t puzzle-pipeline . >/dev/null
if [ "$1" = "--script" ]; then
  shift
  exec docker run --rm -u "$(id -u):$(id -g)" -v "$(pwd):/work" --entrypoint python3 puzzle-pipeline -I "$@"
fi
exec docker run --rm -u "$(id -u):$(id -g)" -v "$(pwd):/work" puzzle-pipeline "$@"
