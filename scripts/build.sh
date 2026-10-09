#!/usr/bin/env bash
# Construit l'image velov-api taguée <version du modèle>-<commit> (+ latest).
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL_VERSION=$(python3 -c "import json; print(json.load(open('models/metadata.json'))['model_version'])")
GIT_SHA=$(git rev-parse --short HEAD)
git diff --quiet HEAD -- . || GIT_SHA="${GIT_SHA}-dirty"

IMAGE=velov-api
docker build \
  --build-arg MODEL_VERSION="$MODEL_VERSION" \
  --build-arg GIT_SHA="$GIT_SHA" \
  -t "$IMAGE:${MODEL_VERSION}-${GIT_SHA}" \
  -t "$IMAGE:latest" \
  .
echo "Image : $IMAGE:${MODEL_VERSION}-${GIT_SHA}"
