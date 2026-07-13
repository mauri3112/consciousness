# Memory Stewardship Experiment

This is a controlled local soak test for the Consciousness and only-memories integration. Its purpose
is to observe whether a sequential team of memory agents makes important knowledge easier to retrieve,
keeps reminders timely, preserves corrections and source history, and prevents low-value repetition
from crowding out durable knowledge.

## Experiment Contract

- Six agent roles run in graph order: Gather, Curate, Synthesize, Validate, Publish, and Audit.
- A single worker executes one role at a time. This keeps SQLite state deterministic and prevents local
  model concurrency.
- Gather through Publish use installed Ornith 1.0 9B Q4. Audit uses the pinned MiniMax M3 supervisor.
  Preflight checks the exact assignments, Ollama residency, and MiniMax provider health.
- Fixture evidence is isolated in `experiment:<EXPERIMENT_ID>` on the `knowledge` plane. Generated
  run recaps are excluded from normal retrieval and, when enabled, belong to `activity`.
- The default run lasts eight hours. The worker advances every five minutes, so each role should run
  roughly sixteen times if the machine remains healthy.
- The supervisor sets the five-minute interval through the durable runtime API and records the change
  as a `runtime.interval` event; existing databases therefore cannot silently retain a faster cadence.
- Nine fixture phases arrive from minute 0 through minute 360. They cover durable axioms, an active
  project, reminders, repetition, low-value noise, a corrected deadline, contradictory evidence,
  authoritative resolution, and a final assessment reminder.
- Consciousness remains the only automatic curator. Fixture injection itself only adds the declared
  test inputs to only-memories.
- Destructive actions and procedure mutations remain approval-gated. Do not auto-approve them during
  the experiment; pending proposals are part of the assessment evidence.

The exact, versionable input corpus is
[`../experiments/memory-stewardship-v1/fixtures.json`](../experiments/memory-stewardship-v1/fixtures.json).
Every experiment directory also receives an immutable copy plus its SHA-256 digest.

## Start Or Resume

Confirm the sibling stack and model, then start the experiment profile:

```bash
docker compose -f ../only-memories/docker-compose.yml up --build -d
ollama pull hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q4_K_M
cp .env.experiment.example .env.experiment
# Set MINIMAX_API_KEY in .env.experiment, then activate the run-two profile once.
docker compose --env-file .env.experiment up --build -d
docker compose --env-file .env.experiment exec api consciousness-upgrade-second-run-profile --apply
docker compose --env-file .env.experiment --profile experiment up --build -d
```

Do not use `docker compose down --volumes`: both project databases are intentionally persistent. The
experiment service mounts both named volumes read-only and uses SQLite's online backup API, so a backup
is consistent even while the worker and memory API are active.

The default local output is:

```text
data/experiments/<EXPERIMENT_ID>/
  manifest.json          environment and model contract at start
  fixtures.json          exact injected corpus
  state.json             restart cursor and memory-id map
  status.json            current heartbeat and progress
  timeline.jsonl         append-only injections, backups, snapshots, failures
  backups/               paired, integrity-checked SQLite copies and hashes
  snapshots/             full agent/run/memory/ranking observations
```

The pre-seed backup is captured before the first fixture is written. Another paired backup follows the
initial seed, every injection phase, each 30-minute interval, and experiment completion. This preserves
enough intermediate states to compare how ranking, versioning, access counts, graph connections, and
agent decisions evolve.

## Monitor Without Perturbing The Test

Use the service health, structured status file, and both operator consoles:

```bash
docker compose --env-file .env.experiment --profile experiment ps
docker compose --env-file .env.experiment logs --tail=100 experiment worker
docker compose --env-file .env.experiment exec experiment consciousness-memory-experiment health
curl -fsS http://localhost:8770/api/v1/runtime
curl -fsS http://localhost:8770/api/v1/metrics
curl -fsS http://localhost:11434/api/ps
```

- Consciousness Studio: `http://localhost:5174`
- only-memories UI: `http://localhost:5173`
- Experiment status: `data/experiments/<EXPERIMENT_ID>/status.json`

Normal observations are one resident Ornith model, MiniMax assigned only to Audit, a running Consciousness runtime, a fresh experiment
heartbeat, steadily increasing successful runs distributed across all six states, and new snapshot and
backup sequence numbers. A `failed` experiment status, stale heartbeat, multiple resident models,
repeated failed runs, a degraded integration, or an integrity error requires investigation. The runner
pauses Consciousness when its own safety checks fail.

## Pause, Resume, Or Stop

Pause only the agents while leaving ingestion/backup supervision active:

```bash
curl -fsS -X POST http://localhost:8770/api/v1/control/pause
```

Resume them:

```bash
curl -fsS -X POST http://localhost:8770/api/v1/control/resume
```

Stop the experiment supervisor without deleting data:

```bash
docker compose --env-file .env.experiment --profile experiment stop experiment
```

Starting the same service with the same `EXPERIMENT_ID` resumes its saved phase cursor and does not
recreate fixture memories already recorded in `state.json`.

## Two-Day Assessment Checklist

Start with `manifest.json`, `status.json`, and the final snapshot. Then compare early, middle, and final
snapshots plus their adjacent database backups.

1. Confirm routine runs used `local/ornith-1.0-9b-q4`, Audit used `minimax/MiniMax-M3`, and no
   snapshot recorded multiple resident Ollama models.
2. Confirm each of the six agent states ran several times, and inspect failures, unresolved risks,
   changed resources, source links, tool calls, and pending approvals.
3. Compare the five fixed search probes. The provenance axiom, current Monday deadline, live reminder,
   runtime constraint, and corrected/versioned information should remain easy to find.
4. Check that `northstar-goal-v2` is current while `northstar-goal-v1` remains available in version
   history. The unverified Friday claim should not outrank the authoritative scheduling source and
   resolution decision.
5. Check whether the repeated source-review reminder created a duplicate-maintenance proposal or was
   otherwise recognized as repetition. Do not treat an unapplied approval-gated proposal as failure.
6. Check that expired tasks/events and low-importance scratch notes fall behind durable project,
   system, axiom, decision, and skill memories.
7. Verify explicit connections make the corrected project, source, resolution, provenance axiom, and
   curation checklist navigable as a useful cluster.
8. Compare backup hashes and run `PRAGMA integrity_check` on restored copies before drawing conclusions.
9. Record where retrieval or agent judgment diverged from the expected behavior. Those observations,
   not a cosmetically clean graph, are the input to the next procedure/ranking iteration.

The final state is intentionally paused after the eight-hour run. Data remains available for the later
assessment even if that assessment begins two days afterward.
