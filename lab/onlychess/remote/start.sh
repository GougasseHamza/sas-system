#!/bin/sh
set -eu

podman network exists sas-ad-lab || podman network create sas-ad-lab
podman volume exists sas-onlychess-db || podman volume create sas-onlychess-db

if ! podman container exists sas-onlychess-db; then
    podman run -d \
        --name sas-onlychess-db \
        --network sas-ad-lab \
        --network-alias db \
        --restart always \
        --memory 512m \
        -e POSTGRES_DB=chess \
        -e POSTGRES_USER=root \
        -e POSTGRES_PASSWORD=chessforever \
        -v sas-onlychess-db:/var/lib/postgresql/data \
        -v /opt/sas-ad-lab/migrations.sql:/docker-entrypoint-initdb.d/001.sql:ro \
        docker.io/library/postgres:16-alpine
else
    podman start sas-onlychess-db >/dev/null
fi

attempt=0
until podman exec sas-onlychess-db pg_isready -U root -d chess >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || { echo "postgres did not become ready" >&2; exit 1; }
    sleep 1
done

if ! podman container exists sas-onlychess; then
    active_image="localhost/sas-onlychess:2025"
    if [ -s /opt/sas-ad-lab/active-image ]; then
        active_image=$(cat /opt/sas-ad-lab/active-image)
    fi
    podman run -d \
        --name sas-onlychess \
        --network sas-ad-lab \
        --restart always \
        --memory 384m \
        -p 4000:4000 \
        "$active_image"
else
    podman start sas-onlychess >/dev/null
fi

attempt=0
until curl -fsS http://127.0.0.1:4000/ >/dev/null; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || { echo "OnlyChess did not become ready" >&2; exit 1; }
    sleep 1
done
