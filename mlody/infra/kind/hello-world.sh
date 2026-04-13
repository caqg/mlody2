#!/usr/bin/env bash
# Smoketest: build a minimal HTTP server image, push it to the local registry,
# deploy it to the kind cluster, and verify it responds.
#
# Usage: ./hello-world.sh [--registry-port PORT] [--cluster-name NAME]
# Defaults match the provisioner defaults: port 5001, cluster "mlody".

set -euo pipefail

REGISTRY_PORT=5001
CLUSTER_NAME=mlody

while [[ $# -gt 0 ]]; do
  case $1 in
    --registry-port) REGISTRY_PORT=$2; shift 2 ;;
    --cluster-name)  CLUSTER_NAME=$2;  shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

IMAGE="localhost:${REGISTRY_PORT}/hello-world:latest"
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo "--- Building image in $WORKDIR"

cat > "$WORKDIR/app.py" <<'EOF'
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hello from kind!\n")
    def log_message(self, *_):
        pass  # silence access logs

HTTPServer(("", 8080), Handler).serve_forever()
EOF

cat > "$WORKDIR/Dockerfile" <<'EOF'
FROM python:3.12-slim
COPY app.py .
CMD ["python", "app.py"]
EOF

docker build -t "$IMAGE" "$WORKDIR"
docker push "$IMAGE"

echo "--- Deploying to cluster '$CLUSTER_NAME'"

# Remove any leftover pod from a previous run.
kubectl delete pod hello-world --ignore-not-found

kubectl run hello-world \
  --image="$IMAGE" \
  --port=8080

echo "--- Waiting for pod to be ready"
if ! kubectl wait pod/hello-world --for=condition=Ready --timeout=120s; then
  echo ""
  echo "Pod did not become ready. Current state:"
  kubectl get pod hello-world
  echo ""
  echo "Events (check for ErrImagePull / ImagePullBackOff):"
  kubectl describe pod hello-world | grep -A 20 "^Events:"
  exit 1
fi

LOCAL_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); p=s.getsockname()[1]; s.close(); print(p)")

echo "--- Testing (port-forward on :${LOCAL_PORT})"
kubectl port-forward pod/hello-world "${LOCAL_PORT}:8080" &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null; rm -rf "$WORKDIR"' EXIT

# Give the tunnel a moment to open.
sleep 1

RESPONSE=$(curl -sf "http://localhost:${LOCAL_PORT}")
kill $PF_PID 2>/dev/null

if [[ "$RESPONSE" == "Hello from kind!" ]]; then
  echo "OK: $RESPONSE"
else
  echo "FAIL: unexpected response: '$RESPONSE'" >&2
  exit 1
fi
