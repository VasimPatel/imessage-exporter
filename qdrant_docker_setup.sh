#!/usr/bin/env bash

# Simple helper to run a local Qdrant instance in Docker.
# Configure via environment variables:
#   CONTAINER_NAME  - Docker container name (default: qdrant-vasim)
#   QDRANT_IMAGE    - Image tag to run (default: qdrant/qdrant:latest)
#   HTTP_PORT       - Host port for HTTP API (default: 6333)
#   GRPC_PORT       - Host port for gRPC API (default: 6334)
#   VOLUME_NAME     - Named volume for persistent storage (default: qdrant_storage)
#   CPU_LIMIT       - CPU cores limit (default: 2)
#   MEMORY_LIMIT    - Memory limit (default: 4g)
#
# Example:
#   CONTAINER_NAME=my-qdrant HTTP_PORT=7000 GRPC_PORT=7001 CPU_LIMIT=4 MEMORY_LIMIT=8g ./qdrant_docker_setup.sh

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-qdrant-vasim}"
QDRANT_IMAGE="${QDRANT_IMAGE:-qdrant/qdrant:latest}"
HTTP_PORT="${HTTP_PORT:-6333}"
GRPC_PORT="${GRPC_PORT:-6334}"
VOLUME_NAME="${VOLUME_NAME:-qdrant_storage}"
CPU_LIMIT="${CPU_LIMIT:-2}"
MEMORY_LIMIT="${MEMORY_LIMIT:-4g}"

function require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: '$1' is required. Please install Docker first: https://docs.docker.com/get-docker/" >&2
    exit 1
  fi
}

function ensure_docker_running() {
  if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker daemon does not appear to be running." >&2
    exit 1
  fi
}

function stop_existing_container() {
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
      echo "Stopping existing container '$CONTAINER_NAME'..."
      docker stop "$CONTAINER_NAME" >/dev/null
    fi

    echo "Removing existing container '$CONTAINER_NAME'..."
    docker rm "$CONTAINER_NAME" >/dev/null
  fi
}

require_command docker
ensure_docker_running

echo "Pulling Qdrant image: $QDRANT_IMAGE"
docker pull "$QDRANT_IMAGE"

stop_existing_container

echo "Starting Qdrant container '$CONTAINER_NAME'..."
echo "Resource limits: CPU=${CPU_LIMIT}, Memory=${MEMORY_LIMIT}"
docker run -d \
  --name "$CONTAINER_NAME" \
  --cpus "${CPU_LIMIT}" \
  --memory "${MEMORY_LIMIT}" \
  -p "${HTTP_PORT}:6333" \
  -p "${GRPC_PORT}:6334" \
  -v "${VOLUME_NAME}:/qdrant/storage" \
  "$QDRANT_IMAGE" >/dev/null

echo "Qdrant is running."
echo "HTTP API: http://localhost:${HTTP_PORT}"
echo "gRPC API: http://localhost:${GRPC_PORT}"
echo "Storage volume: ${VOLUME_NAME}"
