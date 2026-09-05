# Deploying HELIOS

Building the image, running it, and what changes when the same image is
promoted to a production host. `docs/RUNTIME.md` covers what the service does
once it is running.

The governing rule, from the PID: **production runs the same proven image**, and
**only external config, secrets and manifests differ by environment**. Every
decision below follows from that.

---

## 1. Building

```sh
docker build -t helios:<version> .
```

The build has two stages. The first installs the distribution into a virtual
environment; the second copies that environment into a fresh base and is what
ships — so no compiler, no build cache and no source tree reaches the running
image.

### 1.1 What the image is

| | |
|---|---|
| **base** | pinned by **digest**, not by tag, and shared by both stages |
| **user** | non-root (`helios`, uid/gid 10001), no login shell |
| **writable** | one path: `/var/lib/helios`. Everything else can be mounted read-only |
| **ports** | none. Nothing binds, listens or is exposed |
| **secrets** | none, and none are needed: evaluating strategies against market facts requires no credential |
| **configuration** | none baked in. Every `HELIOS_*` setting is injected at run time |
| **health** | `HEALTHCHECK` runs `python3 -m helios.runtime.health` — a process, not an endpoint |

A tag is a moving reference: the same `Dockerfile` built a month apart against
a tag would produce different images, and HELIOS's determinism claim is about a
build rather than a name. Hence the digest. Changing the base is therefore a
deliberate, reviewable edit.

### 1.2 What ships as image data

The v1 market-fact feed (`fixtures/hermes/scenario/` → `/opt/helios/facts`) and
the HSA strategy handoff (`fixtures/strategy_packages/scenario/` →
`/opt/helios/strategies`).

These are **data, not configuration**. Which directory the service reads *is*
configuration, so a deployment that supplies its own facts or its own strategy
packages points `HELIOS_RUNTIME_FEED_DIR` and `HELIOS_STRATEGY_PACKAGE_DIR` at
mounted volumes instead — with no rebuild and no different image.

`.dockerignore` keeps the build context to what the image is built from. That
is not an optimisation: what is not sent cannot be baked in by accident. Tests,
docs, local configuration and every fixture set the image does not ship are
excluded.

---

## 2. Running

Every setting is injected. Nothing below is a default.

```sh
docker run -d --name helios \
  -e HELIOS_ENVIRONMENT=dev \
  -e HELIOS_LOG_LEVEL=INFO \
  -e HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER=1.5 \
  -e HELIOS_FRESHNESS_GRACE_SECONDS=60 \
  -e HELIOS_FRESHNESS_ALLOW_INCOMPLETE_FRAMES=false \
  -e HELIOS_ACCEPTED_HERMES_SCHEMA_VERSIONS=hermes.market_fact/1.0.0 \
  -e HELIOS_PUBLICATION_SINK=STREAM \
  -e HELIOS_PUBLICATION_STREAM=STDOUT \
  -e HELIOS_RUNTIME_INSTRUMENT=XAU_USD \
  -e HELIOS_RUNTIME_FEED_DIR=/opt/helios/facts \
  -e HELIOS_STRATEGY_PACKAGE_DIR=/opt/helios/strategies \
  -e HELIOS_RUNTIME_TICK_SECONDS=5 \
  -e HELIOS_RUNTIME_ON_FEED_END=REPEAT \
  -e HELIOS_RUNTIME_STATUS_FILE=/var/lib/helios/status.json \
  -e HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS=60 \
  helios:<version>
```

`--env-file` works identically and keeps the invocation short. A file of
`HELIOS_*` settings is deployment configuration: it belongs with the
deployment, never in this repository.

`HELIOS_CONFIG_FILE` is the other route — mount a TOML file and name it. The
environment still wins over the file, which is what lets one image run
everywhere with only the injected environment differing. See
`config/helios.example.toml`.

### 2.1 Publishing somewhere durable

`STREAM`/`STDOUT` puts canonical JSON lines on stdout while logs go to stderr,
so both reach `docker logs` and remain separable. To write a file a consumer
tails instead:

```sh
docker run -d --name helios \
  -v helios-state:/var/lib/helios \
  -e HELIOS_PUBLICATION_SINK=FILE \
  -e HELIOS_PUBLICATION_PATH=/var/lib/helios/strategy_state.jsonl \
  ... helios:<version>
```

There is deliberately **no network sink and no state service** — the PID
forbids a public internal state service by default. A consumer reads a file or
a stream (`docs/INTEGRATION.md` §1.6).

### 2.2 Checking it

```sh
docker inspect --format '{{.State.Health.Status}}' helios
docker exec helios python3 -m helios.runtime.health
docker exec helios python3 -m helios.runtime.health --check ready
docker logs helios
```

The probe exits `0` when satisfied and prints one JSON line either way. It reads
the container's own configuration, so it needs no arguments.

### 2.3 Stopping and restarting

```sh
docker stop helios      # SIGTERM: finish the evaluation, flush, close, exit 0
docker restart helios
```

The current evaluation finishes, the sink is flushed and closed, a final status
document is written and the reason is logged. Give it a grace period at least
as long as one evaluation; on the shipped feed a stop completes in well under a
second.

With a `FILE` sink, what is published after a restart is **byte-identical** to
what was published before it — the service keeps no store, so it re-derives
everything from the ordered facts and the definitions.

### 2.4 Hardening

Nothing in the image needs to write outside `/var/lib/helios`, needs a
capability, or needs to be root:

```sh
docker run -d --name helios \
  --read-only --tmpfs /tmp \
  -v helios-state:/var/lib/helios \
  --cap-drop ALL --security-opt no-new-privileges \
  ... helios:<version>
```

---

## 3. Promoting the same image

The image is the artefact. Promotion copies it unchanged; only what surrounds
it differs.

| differs by environment | how |
|---|---|
| **which facts** | `HELIOS_RUNTIME_FEED_DIR`, pointed at a mounted volume rather than the shipped set |
| **which strategies** | `HELIOS_STRATEGY_PACKAGE_DIR`, pointed at the HSA handoff that environment is approved to run |
| **freshness policy** | the `HELIOS_FRESHNESS_*` settings |
| **where state is published** | `HELIOS_PUBLICATION_*` |
| **cadence and bounds** | `HELIOS_RUNTIME_TICK_SECONDS`, `HELIOS_RUNTIME_ON_FEED_END`, `HELIOS_RUNTIME_MAX_CYCLES` |
| **log level** | `HELIOS_LOG_LEVEL` |
| **environment name** | `HELIOS_ENVIRONMENT` — carried in every log line |
| **orchestration** | the manifest, restart policy, volumes and resource limits |

| does **not** differ | why |
|---|---|
| the image digest | promotion moves the proven artefact, not a rebuild of it |
| the strategy code | a logic change is a new `strategy_version`, never an in-place edit (`docs/INTEGRATION.md` §4.2) |
| the published contract | `helios.strategy_state/1.0.0`, byte-stable, identical on every host |
| secrets | there are none. HELIOS needs no credential |

### 3.1 What is different about a production host

**Nothing in the application.** What changes is around it:

* facts come from a mounted volume rather than the shipped v1 set. There is no
  live HERMES client in v1, so a live feed is a later work item that changes the
  transport, not the rules on this page;
* strategy packages are the handoff HSA approved for that environment, mounted
  rather than shipped;
* the publication destination is wherever FALCON reads;
* the freshness policy is likely stricter, and `HELIOS_RUNTIME_TICK_SECONDS` is
  set to the real evaluation cadence rather than a demonstration one;
* the orchestrator supplies the restart policy and resource limits, and drives
  the same `HEALTHCHECK` this image already declares;
* `HELIOS_LOG_LEVEL` is `INFO` rather than `DEBUG`; freshness assessments are
  `DEBUG` and refusals are `WARNING`, so `INFO` keeps the volume down without
  losing the refusals.

### 3.2 Before promoting

1. the test suite passes, including `tests/test_runtime_docker.py`, which
   **builds and runs this image**;
2. the image was built from a clean checkout at a known commit;
3. the strategy packages that environment will mount resolve as a handoff —
   `helios.integration.load_handoff` refuses anything unresolved, and the
   service refuses to start on one;
4. the fact documents that environment will mount are one instrument, one
   cadence, one document per timeframe — the service refuses to start otherwise;
5. the destination directories exist. HELIOS creates no destination it was not
   configured for, and a mistyped path is a startup failure rather than a
   silently unread file.

---

## 4. Troubleshooting

| symptom | what it means |
|---|---|
| exits `2` immediately, one JSON line on stderr | configuration refused. The line lists **every** problem found, each naming the setting and the variable to set |
| exits `2` naming a strategy package | the handoff would not resolve, or a package does not bind to an implementation this build ships. HELIOS refuses rather than running a chain whose components it cannot identify |
| exits `2` naming the feed | two documents for one timeframe, another instrument, or documents disagreeing about cadence |
| health check never turns healthy | the service has not completed an evaluation. `docker logs` will say why; readiness means *evaluated*, not *started* |
| `WARNING` lines about market facts | a fact is older than the configured limit, or a bar is incomplete and the policy forbids it. The line names the age and the limit |
| `ERROR` lines about a contained evaluation | one strategy raised. It published an explicit `INVALID` state and every sibling still published correctly — that containment is the design, and the line names the failure type |
| nothing published | check `HELIOS_PUBLICATION_SINK`. `MEMORY` publishes nowhere durable and exists for dry runs |
