#!/bin/sh
set -eu
podman stop -t 10 sas-onlychess 2>/dev/null || true
podman stop -t 10 sas-onlychess-db 2>/dev/null || true
