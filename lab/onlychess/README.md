# OnlyChess local gate

The vendored service lives at:

```text
vendor/sasctf-finals-2025/onlychess/service
```

Start the untouched service:

```bash
docker compose -p sas_onlychess_mvp \
  -f vendor/sasctf-finals-2025/onlychess/service/compose.yaml \
  up -d --build
```

Run the local synthetic flag round-trip:

```bash
uv run python lab/onlychess/checker_roundtrip.py
```

Reproduce the missing chat-participant checks using three synthetic accounts:

```bash
uv run python lab/onlychess/chat_auth_probe.py
```

The probe exits 0 while either unauthorized ciphertext/IV observation or private
message injection remains possible. It exits 10 only when both paths are blocked,
which lets the candidate gate distinguish a real fix from an inconclusive error.

Verify any candidate source tree in a fresh disposable database/network:

```bash
uv run python lab/onlychess/verify_candidate.py \
  --source-dir vendor/sasctf-finals-2025/onlychess/service
```

For patch approval, also supply a previously proven exploit command. The command
must succeed against the unpatched service and return non-zero against the
candidate:

```bash
uv run python lab/onlychess/verify_candidate.py \
  --source-dir /path/to/candidate/worktree \
  --must-block-command-json '["python3","exploit.py","{base_url}"]' \
  --must-block-returncode 10
```

Stop it without deleting the database:

```bash
docker compose -p sas_onlychess_mvp \
  -f vendor/sasctf-finals-2025/onlychess/service/compose.yaml \
  down
```

Agents are given only the `service/` directory. The official `README.md` and
`exploit/` directories contain solutions and are excluded from blind runs.
