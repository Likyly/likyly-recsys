#!/usr/bin/env bash
# Opens the production MLflow UI on http://localhost:5000, for as long as this runs.
#
# Production has no MLflow server (the droplet has no RAM to spare for one): recsys-api
# records every training run in a SQLite file on the model volume
# (/srv/model/mlflow/mlflow.db, model files under /srv/model/mlflow/artifacts). This starts
# the official MLflow image on the droplet against that file, reachable only through an SSH
# tunnel (bound to 127.0.0.1, never public), and removes the container on Ctrl-C.
#
#   scripts/mlflow-ui.sh            # then open http://localhost:5000
#   PORT=5001 scripts/mlflow-ui.sh  # another local port
set -euo pipefail

HOST="${MLFLOW_SSH_HOST:-do-deployer}"
PORT="${PORT:-5000}"
IMAGE="ghcr.io/mlflow/mlflow:v3.18.0"   # same version as mlflow-skinny in requirements.txt

echo "MLflow UI -> http://localhost:${PORT}  (Ctrl-C to stop it)"
exec ssh -t -L "${PORT}:127.0.0.1:5999" "$HOST" "
  docker rm -f mlflow-ui >/dev/null 2>&1 || true
  # The image is public, but the droplet's stored ghcr.io login (deploy token) makes ghcr
  # answer 'denied' for it: pull it anonymously, with an empty Docker config.
  if ! docker image inspect ${IMAGE} >/dev/null 2>&1; then
    d=\$(mktemp -d); DOCKER_CONFIG=\$d docker pull -q ${IMAGE}; rm -rf \$d
  fi
  docker run --rm --name mlflow-ui -p 127.0.0.1:5999:5000 \
    -e MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false \
    -v licenses_recsys_model_data:/srv/model \
    ${IMAGE} \
    mlflow ui --host 0.0.0.0 --port 5000 --workers 1 \
      --backend-store-uri sqlite:////srv/model/mlflow/mlflow.db \
      --allowed-hosts 'localhost:${PORT},127.0.0.1:${PORT}'
"
