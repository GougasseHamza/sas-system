---
name: arena-service-bootstrap
description: Onboard an unknown authorized Attack-Defense CTF service into SAS Agent Arena by creating a generic descriptor, behavioral dossier, and deterministic hooks. Use when a new competition service or replay fixture is first supplied.
---

# Arena service bootstrap

Work only from the supplied event files, service source, checker, deployment artifacts, and authorized targets.

1. Identify how to build, start, stop, health-check, and run the checker. Put commands in the service TOML descriptor as argv arrays; do not encode service behavior in Python.
2. Map trust boundaries, identities, flag placement/retrieval, persistent state, network protocols, and tick-sensitive behavior.
3. Create deterministic `health` and `checker` hooks first. Add `integrity` or `traffic` hooks only when they produce stable machine signals.
4. Record uncertain observations as evidence with a next test. Do not promote them to service facts.
5. Validate the descriptor with `sas-arena event-validate <event.toml>` and run monitors locally before enabling any network scope.

The output is a service descriptor plus a compact dossier. It is not an exploit or permission to deploy.
