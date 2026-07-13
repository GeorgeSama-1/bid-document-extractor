# GPU Document Extraction Web Service Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-instance intranet Web service that uploads one PDF per job, runs the existing pipeline on a selected server GPU with per-job VLM settings, tracks/cancels jobs, and downloads individual results or a ZIP.

**Architecture:** Extend the existing FastAPI application with focused job-service modules. SQLite persists sanitized metadata, an in-memory secret store holds API keys, and a FIFO worker per physical GPU launches the existing CLI in an isolated subprocess with `CUDA_VISIBLE_DEVICES`. The existing static page gains submission and job-monitoring panels while retaining result browsing.

**Tech Stack:** Python 3.10+, FastAPI, Starlette multipart uploads, SQLite, `subprocess`, `threading`, Uvicorn, native HTML/CSS/JavaScript, pytest.

**Approved spec:** `docs/superpowers/specs/2026-07-13-gpu-web-service-design.md`

**Workspace constraint:** Work in the existing checkout because current uncommitted path-resolution changes affect `app.py` and the CLI. Preserve all pre-existing modifications; stage and commit only files belonging to each task.

---

## Pre-execution checkpoint

- [ ] **Record the exact feature base before Task 1 changes**

Run: `git rev-parse HEAD > .git/gpu-web-service-feature-base`

Expected: `.git/gpu-web-service-feature-base` contains the plan commit hash and does not affect the worktree.

## Chunk 1: Backend foundation

### Task 1: Job models, validation, and SQLite persistence

**Files:**
- Create: `bid_knowledge/service/jobs/__init__.py`
- Create: `bid_knowledge/service/jobs/models.py`
- Create: `bid_knowledge/service/jobs/store.py`
- Test: `tests/test_job_store.py`

- [ ] **Step 1: Write failing model/store tests**

Cover creation, ordered listing, legal state transitions, queue position fields, restart conversion of `queued`/`running` to `failed`, and absence of an API-key column/value.

```python
def test_store_never_persists_api_key(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValidationError):
        JobParameters.model_validate({"vlm_model": "demo", "api_key": "sentinel-secret"})
    assert "api_key" not in JobParameters.model_json_schema()["properties"]
    assert b"api_key" not in (tmp_path / "jobs.sqlite3").read_bytes()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest -q tests/test_job_store.py`

Expected: import failure because `bid_knowledge.service.jobs.store` does not exist.

- [ ] **Step 3: Implement focused models and store**

Define `JobStatus`, immutable/safely serialized job parameters, `JobRecord`, and `JobStore`. Use one SQLite connection per operation, initialize schema idempotently, encode parameter dictionaries as JSON, and centralize status/timestamp updates. Do not accept or expose an API key in any persistent model.

Required public interface:

```python
class JobStore:
    def create(self, job: JobRecord) -> JobRecord: ...
    def get(self, job_id: str) -> JobRecord | None: ...
    def list(self) -> list[JobRecord]: ...
    def update_status(self, job_id: str, status: JobStatus, **fields) -> JobRecord: ...
    def update_progress(self, job_id: str, step: int, total: int, stage: str) -> None: ...
    def mark_interrupted(self) -> int: ...
```

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/test_job_store.py`

Expected: all tests pass.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add bid_knowledge/service/jobs/__init__.py bid_knowledge/service/jobs/models.py bid_knowledge/service/jobs/store.py tests/test_job_store.py
git commit -m "feat: persist sanitized extraction jobs"
```

### Task 2: GPU discovery and safe result files

**Files:**
- Create: `bid_knowledge/service/jobs/gpu.py`
- Create: `bid_knowledge/service/jobs/files.py`
- Test: `tests/test_job_gpu.py`
- Test: `tests/test_job_files.py`

- [ ] **Step 1: Write failing GPU inventory tests**

Inject a command runner and cover CSV parsing, physical IDs, missing `nvidia-smi`, empty output, and rejecting a nonexistent selected ID.

```python
def test_inventory_preserves_physical_gpu_index():
    inventory = NvidiaSmiInventory(run=lambda *_args, **_kwargs: completed("6, NVIDIA A100, 100, 80000"))
    assert inventory.list()[0].id == "6"
```

- [ ] **Step 2: Write failing file safety tests**

Cover nested file enumeration, path traversal, symlink exclusion from tree/download/archive, successful file resolution, ZIP mechanics, and concurrent archive requests using a single per-job lock. Success-state authorization is tested at the manager/API boundary in Tasks 4–5.

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest -q tests/test_job_gpu.py tests/test_job_files.py`

Expected: imports fail because the modules do not exist.

- [ ] **Step 4: Implement GPU inventory and safe traversal**

Execute exactly:

```text
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv,noheader,nounits
```

Parse each nonempty row as `index,name,total_mib,used_mib`, trimming whitespace and converting the two memory fields to integers. Any malformed row raises `GpuInventoryError` rather than returning a partial inventory. Implement one safe iterator shared by file-tree, file-resolution, and ZIP code. Reject absolute paths and `..`, use `{file_path:path}` compatible relative paths, skip every symlink, verify resolved files remain under the output root, and create archives via a temporary file plus `os.replace`.

Required public interface:

```python
class NvidiaSmiInventory:
    def list(self) -> list[GpuInfo]: ...
    def require(self, gpu_id: str) -> GpuInfo: ...

class JobFiles:
    def list(self, output_root: Path) -> list[OutputFile]: ...
    def resolve(self, output_root: Path, relative_path: str) -> Path: ...
    def archive(self, job_id: str, output_root: Path, archive_root: Path) -> Path: ...
```

`JobFiles` only handles safe filesystem mechanics. The success-only authorization for archive creation belongs to `JobManager.archive(job_id)` and is tested in Task 4 and Task 5.

- [ ] **Step 5: Run focused tests**

Run: `pytest -q tests/test_job_gpu.py tests/test_job_files.py`

Expected: all tests pass.

- [ ] **Step 6: Commit only Task 2 files**

```bash
git add bid_knowledge/service/jobs/gpu.py bid_knowledge/service/jobs/files.py tests/test_job_gpu.py tests/test_job_files.py
git commit -m "feat: discover GPUs and secure job downloads"
```

### Task 3: Secret handling, subprocess runner, and GPU scheduler

**Files:**
- Create: `bid_knowledge/service/jobs/secrets.py`
- Create: `bid_knowledge/service/jobs/runner.py`
- Create: `bid_knowledge/service/jobs/scheduler.py`
- Test: `tests/test_job_runner.py`
- Test: `tests/test_job_scheduler.py`

- [ ] **Step 1: Write failing secret/runner tests**

Verify exact redaction before logs/errors are persisted, API Key absence from argv, presence only in child environment, `CUDA_VISIBLE_DEVICES` physical selection, `--pp-structure-device gpu`, progress parsing, nonzero exit handling, and process-group termination.

```python
def test_runner_keeps_key_out_of_argv(fake_popen):
    runner.run(job, api_key="sentinel-key")
    argv, env = fake_popen.last_call
    assert "sentinel-key" not in " ".join(argv)
    assert env["VLM_API_KEY"] == "sentinel-key"
    assert env["CUDA_VISIBLE_DEVICES"] == "6"
```

- [ ] **Step 2: Write failing scheduler tests**

Use a controllable fake runner. Assert FIFO serialization for two jobs on GPU 6, overlap for jobs on GPUs 6 and 7, correct queue position, cancellation before start, cancellation while running, secret cleanup for every terminal state, and shutdown waiting for child termination.

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest -q tests/test_job_runner.py tests/test_job_scheduler.py`

Expected: imports fail because runner/scheduler modules do not exist.

- [ ] **Step 4: Implement secret store and redactor**

Implement a lock-protected in-memory mapping. Redaction must replace the exact nonempty key with `[REDACTED]`; all runner output and exception text passes through it before callbacks or file writes.

- [ ] **Step 5: Implement subprocess runner**

Construct the current CLI invocation from sanitized job parameters, use `start_new_session=True`, merge stdout/stderr, parse `^\[(\d+)/(\d+)\]\s*(.*)$`, and expose a cancellation handle that sends `SIGTERM` to the process group, waits, then sends `SIGKILL` after timeout.

The argv test must assert the complete invocation contains `--pdf`, `--out-dir`, `--path-root`, `--enable-pp-structure true`, `--pp-structure-device gpu`, each of the three `--pp-structure-use-*` flags, `--enable-vlm-table true`, `--vlm-table-endpoint`, `--vlm-table-model`, `--vlm-table-api-key-env VLM_API_KEY`, `--vlm-table-timeout`, `--vlm-table-max-tokens`, `--vlm-table-workers`, and `--progress true`.

Use these explicit contracts:

```python
@dataclass
class RunCallbacks:
    on_started: Callable[[int, "RunningProcess"], None]
    on_output: Callable[[str], None]
    on_progress: Callable[[int, int, str], None]

@dataclass
class RunResult:
    exit_code: int
    cancelled: bool
    error: str | None

class RunningProcess:
    def terminate(self, timeout_seconds: float = 30) -> None: ...
    def wait(self) -> int: ...

class SubprocessJobRunner:
    def run(self, job: JobRecord, api_key: str, callbacks: RunCallbacks) -> RunResult: ...
```

- [ ] **Step 6: Implement one FIFO worker per GPU**

Each worker owns one `queue.Queue`; terminal callbacks update the store and clear secrets in `finally`. Scheduler shutdown rejects new submissions, cancels queued jobs, terminates running handles, joins workers, and only then returns.

Scheduler contract:

```python
class GpuJobScheduler:
    def submit(self, job: JobRecord) -> None: ...
    def cancel(self, job_id: str) -> bool: ...
    def queue_position(self, job_id: str) -> int | None: ...
    def shutdown(self, timeout_seconds: float = 30) -> None: ...
```

Cancellation races are resolved under one scheduler lock. A running job reaches `cancelled` only after `RunningProcess.terminate()` and `wait()` confirm exit; store update happens before secret deletion in the worker `finally` block.

- [ ] **Step 7: Run focused tests**

Run: `pytest -q tests/test_job_runner.py tests/test_job_scheduler.py`

Expected: all tests pass without real GPU/PaddleOCR.

- [ ] **Step 8: Commit only Task 3 files**

```bash
git add bid_knowledge/service/jobs/secrets.py bid_knowledge/service/jobs/runner.py bid_knowledge/service/jobs/scheduler.py tests/test_job_runner.py tests/test_job_scheduler.py
git commit -m "feat: schedule isolated jobs per GPU"
```

## Chunk 2: Service API and UI

### Task 4: Job manager and upload validation

**Files:**
- Create: `bid_knowledge/service/jobs/manager.py`
- Test: `tests/test_job_manager.py`

- [ ] **Step 1: Write failing manager tests**

Cover PDF extension/header, 500 MiB default limit via an injected smaller test limit, partial-upload cleanup, URL validation, exact numeric ranges, required GPU, required endpoint/model, all three PP-Structure boolean defaults/parsing, acceptance of an empty API Key, invalid/non-positive environment overrides, generated directory layout, `run_name`, startup interruption handling, and no Key in returned records. Inject store/secret/scheduler failures one at a time and assert created files, records, and secrets are rolled back or moved to an explicit failed terminal state without leaving a queued half-task.

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest -q tests/test_job_manager.py`

Expected: import failure because manager does not exist.

- [ ] **Step 3: Implement manager orchestration**

Build `JobManager` from injected store, inventory, scheduler, files, and roots. Read upload streams in chunks, validate `%PDF`, use a temporary upload followed by atomic rename, validate all form values before creating the job, and submit the sanitized job plus Key separately.

Public service contract:

```python
class JobManager:
    def list_gpus(self) -> list[GpuInfo]: ...
    def create_job(self, upload: BinaryIO, filename: str, form: Mapping[str, str], api_key: str) -> JobView: ...
    def list_jobs(self) -> list[JobView]: ...
    def get_job(self, job_id: str) -> JobView: ...
    def cancel(self, job_id: str) -> JobView: ...
    def tail_log(self, job_id: str, limit: int = 200) -> list[str]: ...
    def list_files(self, job_id: str) -> list[OutputFile]: ...
    def resolve_file(self, job_id: str, relative_path: str) -> Path: ...
    def archive(self, job_id: str) -> Path: ...  # requires succeeded
    def start(self) -> None: ...
    def shutdown(self) -> None: ...
```

Inject `SecretStore` directly into `JobManager`. After persistence succeeds, call `secrets.put(job_id, api_key)` and then `scheduler.submit(job)`. If secret insertion fails, delete the upload/output staging and transition or remove the just-created record consistently. If scheduler submission fails, immediately `secrets.delete(job_id)`, clean staging, and mark the record `failed`; tests assert no retained Key and no `queued` half-task. `GpuJobScheduler.submit(job)` retrieves the Key from its injected shared `SecretStore` only when the worker starts.

`JobView` is a response-safe type containing no secret. It includes ID, status, queue position, progress step/total/stage, sanitized parameters, original filename, GPU, timestamps, error, log tail, and `run_name` only when status is `succeeded`.

Required validation defaults: upload 500 MiB (`BID_SERVICE_MAX_UPLOAD_BYTES`), timeout 1800 in 1–3600, tokens 8192 in 256–32768, workers 16 in 1–128 capped by `BID_SERVICE_MAX_VLM_WORKERS`, model length 1–200, path root length 1–100, and absolute credential-free HTTP(S) endpoint.

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/test_job_manager.py`

Expected: all tests pass.

- [ ] **Step 5: Commit only Task 4 files**

```bash
git add bid_knowledge/service/jobs/manager.py tests/test_job_manager.py
git commit -m "feat: validate and create web extraction jobs"
```

### Task 5: FastAPI job endpoints and application lifecycle

**Files:**
- Modify: `bid_knowledge/service/app.py`
- Create: `bid_knowledge/service/jobs/instance_lock.py`
- Modify: `requirements.txt`
- Test: `tests/test_job_api.py`
- Test: `tests/test_service_lifecycle.py`
- Test: `tests/test_result_browser.py`

- [ ] **Step 1: Write failing API tests**

Use a fake `JobManager` through `create_app(job_manager=...)`. Cover GPU listing, manual multipart parsing, create/list/detail/cancel, nested file route, archive, 404s, invalid form errors without Key echo, path traversal through `{file_path:path}`, symlink rejection at the HTTP boundary, archive rejection for non-succeeded jobs, and preservation of all existing result-browser routes.

```python
def test_create_error_never_echoes_key(client):
    response = client.post("/api/jobs", data={"api_key": "sentinel-key"}, files={"pdf": ("x.pdf", b"bad")})
    assert response.status_code == 400
    assert "sentinel-key" not in response.text
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest -q tests/test_job_api.py tests/test_result_browser.py`

Expected: new endpoint tests fail while existing browser tests show the compatibility baseline.

- [ ] **Step 3: Refactor app creation without breaking imports**

Add `create_app(job_manager: JobManager | None = None) -> FastAPI`, keep module-level `app = create_app()`, and attach startup/shutdown through a lifespan context. Production creation acquires the single-instance file lock, initializes storage, marks interrupted jobs, and starts/stops the scheduler. Tests inject a fake manager and never call `nvidia-smi`.

Create `InstanceLock` using `fcntl.flock(..., LOCK_EX | LOCK_NB)`. It keeps the open file descriptor for the entire lifespan, releases it on startup failure and shutdown, and raises a clear `ServiceAlreadyRunningError` for a second instance. Add focused lifecycle tests proving exclusive acquisition and the shutdown order `stop accepting -> manager.shutdown (waits children) -> lock.release`.

- [ ] **Step 4: Add job endpoints**

Manually extract multipart fields from `Request.form()`, remove Key immediately, pass it separately as `manager.create_job(..., form=sanitized_form, api_key=api_key)`, normalize errors through the Key redactor, and return explicit response dictionaries that exclude secret data. Add `python-multipart` to requirements.

Stable job JSON fields are: `id`, `status`, `queue_position`, `gpu_id`, `original_filename`, `parameters`, `progress: {step,total,stage}`, `created_at`, `started_at`, `finished_at`, `error`, `logs`, and `run_name`. List responses wrap them as `{"jobs": [...]}`; GPU responses use `{"gpus": [...]}`; file tree responses use `{"files": [...]}`. Creation returns 201, lookup/file misses 404, invalid form/GPU/path/status returns 400, already-cancelled terminal jobs return 409, and unexpected sanitized failures return 500. Register every API route before the existing root `StaticFiles` mount.

- [ ] **Step 5: Run API and regression tests**

Run: `pytest -q tests/test_job_api.py tests/test_service_lifecycle.py tests/test_result_browser.py tests/test_mcp_server.py`

Expected: all pass.

- [ ] **Step 6: Commit only Task 5 files**

```bash
git add bid_knowledge/service/app.py bid_knowledge/service/jobs/instance_lock.py requirements.txt tests/test_job_api.py tests/test_service_lifecycle.py tests/test_result_browser.py
git commit -m "feat: expose extraction job API"
```

### Task 6: Browser task form, monitoring, and downloads

**Files:**
- Modify: `bid_knowledge/service/static/index.html`
- Create: `bid_knowledge/service/static/jobs.css`
- Create: `bid_knowledge/service/static/jobs.js`
- Test: `tests/test_service_ui.py`

- [ ] **Step 1: Write a failing static UI smoke test**

Assert presence of exact multipart fields `pdf`, `gpu_id`, `path_root`, `pp_structure_use_doc_orientation_classify`, `pp_structure_use_doc_unwarping`, `pp_structure_use_textline_orientation`, `vlm_endpoint`, `vlm_model`, `api_key`, `vlm_timeout`, `vlm_max_tokens`, and `vlm_workers`; verify documented defaults, create/list/detail/cancel/download controls, password input type, expected API paths, and absence of localStorage usage for API Key.

- [ ] **Step 2: Run test and verify failure**

Run: `pytest -q tests/test_service_ui.py`

Expected: failures for missing job UI elements.

- [ ] **Step 3: Extend the existing page**

Keep `index.html` responsible for markup and existing result-browser markup. Put new job styles in `jobs.css` and all submission/polling/task behavior in `jobs.js` so the already-large HTML file does not accumulate more inline responsibilities. Add a compact job submission panel, GPU dropdown loaded from `/api/system/gpus`, task table, detail/progress/log panel, safe result file tree, individual download links, ZIP button, and existing result-browser integration using successful job `run_name`. Submit exact field names above with `FormData`; defaults are `path_root=PDF`, all three PP flags false, timeout 1800, max tokens 8192, and workers 16. Clear the Key field immediately after a successful response; poll active jobs at a short interval; never write Key to URL, DOM logs, or browser storage.

- [ ] **Step 4: Run UI and API tests**

Run: `pytest -q tests/test_service_ui.py tests/test_job_api.py`

Expected: all pass.

- [ ] **Step 5: Commit only Task 6 files**

```bash
git add bid_knowledge/service/static/index.html bid_knowledge/service/static/jobs.css bid_knowledge/service/static/jobs.js tests/test_service_ui.py
git commit -m "feat: manage GPU extraction jobs in browser"
```

## Chunk 3: Deployment and verification

### Task 7: Single-instance production deployment

**Files:**
- Create: `deploy/bid-document-extractor.service.example`
- Create: `deploy/bid-document-extractor.env.example`
- Create: `deploy/SERVER_INSTALL.md`
- Create: `scripts/run_service.py`
- Test: `tests/test_service_deployment.py`

- [ ] **Step 1: Write failing deployment tests**

Assert launcher binds configurable host/port with exactly one worker; env example defines `BID_SERVICE_HOST`, `BID_SERVICE_PORT`, `BID_SERVICE_MAX_UPLOAD_BYTES`, and `BID_SERVICE_MAX_VLM_WORKERS`; unit consumes the env file and contains `KillMode=control-group`, `TimeoutStopSec=45`, restart policy, exact launcher wiring, correct working-directory placeholders, and no embedded secrets.

- [ ] **Step 2: Run test and verify failure**

Run: `pytest -q tests/test_service_deployment.py`

Expected: deployment files are missing.

- [ ] **Step 3: Implement launcher and examples**

Launcher reads `BID_SERVICE_HOST` defaulting to `0.0.0.0` and `BID_SERVICE_PORT` defaulting to `8000`, then runs Uvicorn with one worker only. The systemd example uses the GPU-capable Python environment placeholder, `KillMode=control-group`, `TimeoutStopSec=45`, `Restart=on-failure`, and documents `http://172.20.0.160:8000` plus firewall restriction to the trusted subnet.

Launcher core must be equivalent to:

```python
uvicorn.run(
    "bid_knowledge.service.app:app",
    host=os.getenv("BID_SERVICE_HOST", "0.0.0.0"),
    port=int(os.getenv("BID_SERVICE_PORT", "8000")),
    workers=1,
)
```

The environment example contains:

```text
BID_SERVICE_HOST=0.0.0.0
BID_SERVICE_PORT=8000
BID_SERVICE_MAX_UPLOAD_BYTES=524288000
BID_SERVICE_MAX_VLM_WORKERS=128
```

The unit uses these exact relationships (paths remain explicit placeholders for the server operator):

```ini
[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/ABSOLUTE/PATH/TO/bid_source/bid-document-extractor
EnvironmentFile=/etc/bid-document-extractor.env
ExecStart=/ABSOLUTE/PATH/TO/GPU_ENV/bin/python -m scripts.run_service
KillMode=control-group
TimeoutStopSec=45
Restart=on-failure
```

- [ ] **Step 4: Run deployment tests**

Run: `pytest -q tests/test_service_deployment.py`

Expected: all pass.

- [ ] **Step 5: Commit only Task 7 files**

```bash
git add deploy/bid-document-extractor.service.example deploy/bid-document-extractor.env.example deploy/SERVER_INSTALL.md scripts/run_service.py tests/test_service_deployment.py
git commit -m "chore: add single-instance service deployment"
```

### Task 8: Full regression and security verification

**Files:**
- Create: `tests/test_job_secret_e2e.py`
- Create: `tests/test_service_smoke.py`
- Modify only if failures expose defects in files created above.

- [ ] **Step 1: Run the complete test suite**

Run: `pytest -q`

Expected: all tests pass; GPU/VLM integration remains mocked.

- [ ] **Step 2: Run sensitive-value and dependency checks**

Implement `tests/test_job_secret_e2e.py` with a fake short-lived child process and sentinel `UNIQUE_SENTINEL_API_KEY`. It submits through the API, captures argv/environment separately, completes a job with downloadable output, and generates ZIP. Assert the sentinel is absent from SQLite bytes, log bytes, every API response body, and captured argv while present only in captured child environment. Open the ZIP with `zipfile.ZipFile` and assert the sentinel is absent from every member filename, metadata string, and decompressed member content; checking only compressed archive bytes is insufficient.

Run: `pytest -q tests/test_job_secret_e2e.py`

Expected: all tests pass and no assertion prints the sentinel.

- [ ] **Step 3: Run a local no-GPU API smoke test with injected inventory**

Implement `tests/test_service_smoke.py` using `create_app(job_manager=FakeJobManager())` and `TestClient`. The fake returns an empty GPU/job set and never constructs the production inventory or scheduler. Assert 200 for `/`, `/api/jobs`, and `/api/runs`; do not launch PaddleOCR.

Run: `pytest -q tests/test_service_smoke.py`

Expected: all three route assertions pass.

- [ ] **Step 4: Inspect the final diff for unrelated changes**

Recover the pre-recorded base with `FEATURE_BASE=$(cat .git/gpu-web-service-feature-base)`. Run `git status --short` and `git diff --stat "$FEATURE_BASE"..HEAD`.

Expected: pre-existing user changes remain untouched; feature commits contain only scoped files.

- [ ] **Step 5: Record server activation commands in the handoff**

Write the following operator sequence, with placeholders explained, into `deploy/SERVER_INSTALL.md`; do not execute privileged commands automatically:

```bash
cd /ABSOLUTE/PATH/TO/bid_source/bid-document-extractor
/ABSOLUTE/PATH/TO/GPU_ENV/bin/python -m pip install -r requirements.txt
sudo cp deploy/bid-document-extractor.env.example /etc/bid-document-extractor.env
sudo cp deploy/bid-document-extractor.service.example /etc/systemd/system/bid-document-extractor.service
sudoedit /etc/bid-document-extractor.env
sudoedit /etc/systemd/system/bid-document-extractor.service
sudo systemctl daemon-reload
sudo systemctl enable --now bid-document-extractor.service
sudo systemctl status bid-document-extractor.service
sudo journalctl -u bid-document-extractor.service -f
```

The final documented browser URL is `http://172.20.0.160:8000`.
