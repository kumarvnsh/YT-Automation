# Automated Content Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the existing two-times-daily Shorts pipeline independently resumable, provider-resilient between Claude and OpenAI, and unable to upload a new job until deterministic quality checks pass.

**Architecture:** Keep cron-job.org and the GitHub Actions --step loop as the scheduler boundary. Add persisted job-slot and topic-reservation metadata, route script generation through a small provider policy, and insert a local quality step before upload. Preserve Pexels, ffmpeg, YouTube, Meta, approval, and old-stage resume contracts.

**Tech Stack:** Python 3.11, unittest, PyYAML, existing Anthropic/OpenAI clients, GitHub Actions, ffmpeg/ffprobe, JSON state files.

---

## Scope and File Map

This plan implements the first executable phase approved in the design:

- Independent morning/evening job identity and topic reservation.
- Claude/OpenAI fallback, random, and round-robin routing for script generation.
- Deterministic pre-upload quality validation.
- Unit, pipeline, and workflow regression tests.

Deferred until this phase is stable:

- Analytics-based topic weighting and provider performance selection.
- Selective AI visual fallback and generated-image cost accounting.
- Dashboard or database-backed queue.

Files to modify or create:

- Modify src/script_generator.py for provider routing, output validation, and provider metadata.
- Modify src/topics.py for normalized topic fingerprints and idempotent reservations.
- Create src/quality_gate.py for local artifact checks and isolated ffprobe calls.
- Modify src/pipeline.py for slot/provider/reservation metadata and the quality step.
- Modify config.yaml for routing and quality defaults.
- Modify .github/workflows/publish.yml for IST slot metadata and reservation commits.
- Modify tests/test_workflows.py and tests/test_pipeline_artifacts.py.
- Create tests/test_script_provider_routing.py, tests/test_topics.py, tests/test_quality_gate.py, and tests/test_automation_smoke.py.
- Modify README.md with operations and configuration notes.

## State Contract

New stages should contain:

~~~json
{
  "job_id": "20260711_090000_short",
  "slot": "morning",
  "script_provider": "anthropic",
  "script_fallback_used": false,
  "topic_reservation": {
    "fingerprint": "forgotten_womens_history",
    "status": "reserved"
  },
  "quality": {
    "passed": true,
    "score": 100,
    "checks": {}
  }
}
~~~

Existing stages without these fields remain resumable. The new quality step is
added only to newly created stages. A legacy stage that predates the quality
step may continue through its original target steps; a new stage cannot skip a
failed quality result.

### Task 1: Define Provider Routing

**Files:**

- Create: tests/test_script_provider_routing.py
- Modify: config.yaml
- Modify: src/script_generator.py

- [ ] **Step 1: Write failing routing tests.**

Patch _call_anthropic and _call_openai; never create a real API client. Use this
valid response fixture:

~~~python
SCRIPT_JSON = {
    "topic": "test topic",
    "title": "A Test History Hook",
    "description": "A short description.\\n\\n#history #shorts #facts",
    "tags": ["history", "facts"],
    "segments": [
        {"narration": "This is the hook.", "keywords": ["old library"]},
        {"narration": "This is the explanation.", "keywords": ["historic document"]},
    ],
}
~~~

Cover these cases:

- Legacy script.provider: openai calls only OpenAI.
- routing: fallback uses OpenAI after a Claude exception.
- Invalid Claude JSON also triggers OpenAI fallback.
- routing: random calls exactly the patched provider selected by random.choice.
- Both providers failing raises one error containing both provider errors.
- The returned Script records provider and fallback_used.

- [ ] **Step 2: Run the focused tests and confirm they fail.**

~~~bash
python -m unittest tests.test_script_provider_routing -v
~~~

Expected: failures because Script has no provider metadata and generation still
calls only the legacy provider.

- [ ] **Step 3: Add backward-compatible configuration.**

Keep the existing key and add:

~~~yaml
script:
  provider: "anthropic"
  routing: "fallback"       # fallback | random | round_robin
  primary_provider: "anthropic"
  fallback_provider: "openai"
~~~

If routing is absent, preserve the current single-provider behavior. Do not add
API keys to YAML.

- [ ] **Step 4: Add provider metadata and validated routing.**

Extend Script with defaulted fields so old positional constructors still work:

~~~python
provider: str | None = None
fallback_used: bool = False
~~~

Add these helpers:

~~~python
def _provider_call(cfg: Config, provider: str, prompt: str) -> str:
    if provider == "anthropic":
        return _call_anthropic(cfg, prompt)
    if provider == "openai":
        return _call_openai(cfg, prompt)
    raise RuntimeError(f"Unsupported script provider: {provider}")


def _parse_script_response(raw: str) -> tuple[dict, list[Segment]]:
    data = _extract_json(raw)
    for key in ("title", "description", "segments"):
        if not data.get(key):
            raise ValueError(f"script response missing: {key}")
    segments = [
        Segment(str(item["narration"]).strip(), item.get("keywords", []))
        for item in data["segments"]
        if item.get("narration", "").strip()
    ]
    if not segments:
        raise ValueError("script response has no non-empty segments")
    return data, segments
~~~

In generate_script, derive providers as follows:

~~~python
legacy = cfg.get("script.provider", "anthropic")
routing = cfg.get("script.routing")
if not routing:
    providers = [legacy]
elif routing == "random":
    providers = [random.choice([
        cfg.get("script.primary_provider", legacy),
        cfg.get("script.fallback_provider", "openai"),
    ])]
elif routing == "round_robin":
    providers = [_next_round_robin_provider(cfg)]
else:
    providers = [
        cfg.get("script.primary_provider", legacy),
        cfg.get("script.fallback_provider", "openai"),
    ]
~~~

Call providers in order, parse and validate each response, and return the first
valid Script. Set fallback_used when the successful provider is not the first
attempted provider. Aggregate provider errors in the final exception. Extract
the current tag de-duplication block into a helper without changing its output.

For round-robin, persist the next index in data/provider_rotation.json; tests
patch base_dir to a temporary directory. Read and update that file only for a new
script generation:

~~~python
def _next_round_robin_provider(cfg: Config) -> str:
    providers = [
        cfg.get("script.primary_provider", cfg.get("script.provider", "anthropic")),
        cfg.get("script.fallback_provider", "openai"),
    ]
    path = base_dir() / "data" / "provider_rotation.json"
    try:
        index = int(json.loads(path.read_text(encoding="utf-8")).get("next_index", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        index = 0
    selected = providers[index % len(providers)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"next_index": (index + 1) % len(providers)}), encoding="utf-8")
    return selected
~~~

In pipeline._step_script, save:

~~~python
st["script_provider"] = script.provider
st["script_fallback_used"] = script.fallback_used
~~~

- [ ] **Step 5: Run routing and regression tests.**

~~~bash
python -m unittest tests.test_script_provider_routing -v
python -m unittest discover -s tests -v
~~~

Expected: all tests pass without network calls.

- [ ] **Step 6: Commit.**

~~~bash
git add config.yaml src/script_generator.py src/pipeline.py tests/test_script_provider_routing.py
git commit -m "feat: route script generation across providers"
~~~

### Task 2: Reserve Distinct Topics Per Slot

**Files:**

- Create: tests/test_topics.py
- Modify: src/topics.py
- Modify: src/pipeline.py

- [ ] **Step 1: Write failing reservation tests.**

Use a temporary base_dir. Cover:

~~~python
reservation = topics.reserve_topic(
    "The Forgotten Library of Alexandria", "short", "morning", "job-a"
)
self.assertEqual("morning", reservation["slot"])
self.assertTrue(reservation["fingerprint"])
self.assertTrue((Path(tmp) / "data" / "topic_reservations.json").exists())

topics.reserve_topic("The Forgotten Library of Alexandria", "short", "morning", "job-a")
with self.assertRaisesRegex(ValueError, "duplicate topic rejected"):
    topics.reserve_topic("Forgotten Library Alexandria", "short", "evening", "job-b")

first = topics.reserve_topic("A Strange Roman Law", "short", "morning", "job-c")
second = topics.reserve_topic("A Strange Roman Law", "short", "morning", "job-c")
self.assertEqual(first, second)

evening = topics.reserve_topic("How Rome Built Its Roads", "short", "evening", "job-d")
self.assertEqual("evening", evening["slot"])
~~~

Put each assertion group in its own TestCase method and patch
src.topics.base_dir to Path(tmp). The second reservation for job-a must return
the original record before duplicate matching runs.

- [ ] **Step 2: Run the focused tests and confirm the API is missing.**

~~~bash
python -m unittest tests.test_topics -v
~~~

Expected: failure because reserve_topic and the reservation file do not yet
exist.

- [ ] **Step 3: Implement topic fingerprints and reservations.**

Add:

~~~python
def _reservations_file() -> Path:
    return base_dir() / "data" / "topic_reservations.json"


def topic_fingerprint(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    ignored = {"a", "an", "and", "the", "of", "to", "in", "how", "why"}
    return "_".join(sorted({word for word in words if word not in ignored}))


def reserve_topic(title: str, fmt: str, slot: str, job_id: str) -> dict:
    entries = _load_reservations()
    existing = next((item for item in entries if item["job_id"] == job_id), None)
    if existing:
        return existing
    fingerprint = topic_fingerprint(title)
    if any(_fingerprint_is_near(fingerprint, item["fingerprint"])
           for item in entries[-60:]):
        raise ValueError(f"duplicate topic rejected: {title}")
    reservation = {
        "job_id": job_id,
        "title": title,
        "fingerprint": fingerprint,
        "format": fmt,
        "slot": slot,
        "date": date.today().isoformat(),
        "status": "reserved",
    }
    _write_reservations(entries + [reservation])
    return reservation
~~~

Implement the helpers used above:

~~~python
def _load_reservations() -> list[dict]:
    path = _reservations_file()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_reservations(entries: list[dict]) -> None:
    path = _reservations_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _fingerprint_is_near(left: str, right: str) -> bool:
    return SequenceMatcher(None, left, right).ratio() >= 0.82
~~~

Import re and SequenceMatcher. Before accepting a reservation, compare the
fingerprint against the last 60 reservation fingerprints and against
topic_fingerprint(title) for each title returned by recent_titles(60). Keep the
existing record_topic function unchanged.

- [ ] **Step 4: Add stage identity and reservation after script generation.**

In new_stage:

~~~python
job_id = f"{ts}_{fmt}"
state.update({
    "job_id": job_id,
    "slot": env("PUBLISH_SLOT", "manual"),
})
~~~

In _step_script, after generation and before writing script files:

~~~python
st["topic_reservation"] = topics.reserve_topic(
    script.title,
    st["fmt"],
    st.get("slot", "manual"),
    st["job_id"],
)
~~~

The reservation function is idempotent, so a retry after a partial write does not
create a second reservation.

- [ ] **Step 5: Run focused tests and commit.**

~~~bash
python -m unittest tests.test_topics tests.test_pipeline_artifacts -v
git add src/topics.py src/pipeline.py tests/test_topics.py tests/test_pipeline_artifacts.py
git commit -m "feat: reserve distinct topics per publish slot"
~~~

Expected: all focused tests pass and existing artifact tests remain green.

### Task 3: Insert the Deterministic Quality Gate

**Files:**

- Create: src/quality_gate.py
- Create: tests/test_quality_gate.py
- Modify: src/pipeline.py
- Modify: config.yaml

- [ ] **Step 1: Write failing quality tests.**

Build temporary stage fixtures with a script, voiceover, captions, asset, and
video path. Patch subprocess.run to return ffprobe JSON. The shared state is:

~~~python
valid_state = {
    "fmt": "short",
    "title": "A Test History Hook",
    "script": {
        "title": "A Test History Hook",
        "description": "Test description",
        "tags": ["history"],
        "segments": [{"narration": "One two three four five.", "keywords": ["old book"]}],
    },
    "voiceover": {"path": "voiceover.mp3", "duration": 20.0, "words": [["One", 0.0, 0.2]]},
    "captions": "captions.ass",
    "assets": [[["assets/seg_00.jpg", False]]],
    "video": "video.mp4",
    "topic_reservation": {"status": "reserved"},
}
ffprobe_run.return_value.stdout = json.dumps({
    "streams": [
        {"codec_type": "video", "width": 1080, "height": 1920},
        {"codec_type": "audio"},
    ],
    "format": {"duration": "20.0"},
})
~~~

Cover:

~~~python
report = validate_stage(cfg, stage, valid_state)
self.assertTrue(report["passed"])
self.assertEqual("pass", report["checks"]["video"])

(stage / "voiceover.mp3").unlink()
report = validate_stage(cfg, stage, valid_state)
self.assertFalse(report["passed"])
self.assertEqual("fail", report["checks"]["audio"])

ffprobe_run.return_value.stdout = json.dumps({
    "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080},
        {"codec_type": "audio"},
    ],
    "format": {"duration": "20.0"},
})
report = validate_stage(cfg, stage, valid_state)
self.assertFalse(report["passed"])
self.assertEqual("fail", report["checks"]["dimensions"])

with patch("src.quality_gate.validate_stage", return_value={
    "passed": False,
    "checks": {"audio": "fail"},
    "errors": ["voiceover missing"],
    "score": 0,
}):
    with self.assertRaisesRegex(RuntimeError, "quality gate failed: audio"):
        pipeline._step_quality(cfg, stage, valid_state)
~~~

Use exact check names: script, topic, narration, audio, captions, assets,
video, dimensions, duration, safety, and metadata.

- [ ] **Step 2: Run the focused tests and confirm the module is missing.**

~~~bash
python -m unittest tests.test_quality_gate -v
~~~

Expected: import or attribute failures because quality_gate.py and
pipeline._step_quality do not exist.

- [ ] **Step 3: Implement the local quality report.**

Create this public function:

~~~python
def validate_stage(cfg: Config, stage: Path, st: dict) -> dict:
    """Return passed, score, checks, and errors without making API calls."""
~~~

Use ffprobe with:

~~~python
subprocess.run(
    ["ffprobe", "-v", "error", "-show_streams", "-show_format",
     "-of", "json", str(stage / "video.mp4")],
    check=True, capture_output=True, text=True,
)
~~~

Check script structure, title/description/tags, duplicate reservation, target
narration length, nonempty audio, timed captions when enabled, every stored
asset path, video existence, configured width/height, duration ratio, and
nonempty audio/video streams. Check configured banned terms deterministically:

~~~yaml
quality:
  enabled: true
  min_duration_ratio: 0.75
  max_duration_ratio: 1.35
  banned_terms: ["graphic sexual", "extreme gore"]
~~~

The score is the percentage of checks passing. passed is false if any required
check fails; score cannot override failed audio, video, dimensions, or safety.

- [ ] **Step 4: Add the quality step before upload.**

Change the new-stage order:

~~~python
STEP_ORDER = [
    "script", "voiceover", "captions", "assets", "render", "compose",
    "quality", "upload",
]
~~~

Add:

~~~python
def _step_quality(cfg: Config, stage: Path, st: dict) -> None:
    from .quality_gate import validate_stage
    report = validate_stage(cfg, stage, st)
    st["quality"] = report
    (stage / "quality.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if not report["passed"]:
        failed = ", ".join(
            name for name, status in report["checks"].items() if status == "fail"
        )
        raise RuntimeError(f"quality gate failed: {failed}")
~~~

Register it in STEP_FUNCS. At the beginning of _step_upload, reject a new stage
with a missing or failed quality report:

~~~python
if "quality" in st and not st["quality"].get("passed", False):
    raise RuntimeError("upload blocked because quality gate did not pass")
~~~

Legacy stages with no quality field remain compatible with the existing
approval/resume flow. New stages always include the quality step in their target
list.

- [ ] **Step 5: Run focused and full tests.**

~~~bash
python -m unittest tests.test_quality_gate tests.test_pipeline_artifacts -v
python -m unittest discover -s tests -v
~~~

Expected: all tests pass and no unit test starts ffmpeg or makes a network call.

- [ ] **Step 6: Commit.**

~~~bash
git add src/quality_gate.py src/pipeline.py config.yaml tests/test_quality_gate.py
git commit -m "feat: block uploads until quality checks pass"
~~~

### Task 4: Export IST Slot and Persist Reservations in Actions

**Files:**

- Modify: .github/workflows/publish.yml
- Modify: src/pipeline.py
- Modify: tests/test_workflows.py
- Modify: tests/test_pipeline_artifacts.py

- [ ] **Step 1: Write failing workflow/state assertions.**

Add tests asserting that the workflow contains PUBLISH_SLOT, Asia/Kolkata, and
data/topic_reservations.json; assert that _record_published stores the slot from
PUBLISH_SLOT.

- [ ] **Step 2: Run the focused tests and confirm the contract is absent.**

~~~bash
python -m unittest tests.test_workflows tests.test_pipeline_artifacts -v
~~~

Expected: only the new slot/reservation assertions fail.

- [ ] **Step 3: Derive morning/evening from IST in the existing workflow.**

Before the pipeline loop, add:

~~~bash
HOUR_IST=$(TZ=Asia/Kolkata date +%H)
if [ "$HOUR_IST" -lt 14 ]; then
  export PUBLISH_SLOT=morning
else
  export PUBLISH_SLOT=evening
fi
echo "Using publish slot: $PUBLISH_SLOT"
~~~

Do not change cron-job.org, the 45-minute duplicate guard, or the existing
maximum-two-per-IST-day guard.

- [ ] **Step 4: Commit reservation state and include slot in published index.**

Add data/topic_reservations.json to the existing workflow git add command. In
_record_published, add:

~~~python
"slot": st.get("slot") or env("PUBLISH_SLOT") or "manual",
~~~

Preserve all existing published-index fields.

- [ ] **Step 5: Run regression tests and commit.**

~~~bash
python -m unittest tests.test_workflows tests.test_pipeline_artifacts -v
python -m unittest discover -s tests -v
git add .github/workflows/publish.yml src/pipeline.py tests/test_workflows.py tests/test_pipeline_artifacts.py
git commit -m "feat: persist publish slots and topic reservations"
~~~

### Task 5: Add Mocked End-to-End Verification and Documentation

**Files:**

- Create: tests/test_automation_smoke.py
- Modify: README.md

- [ ] **Step 1: Write a mocked pipeline smoke test.**

Patch script calls, TTS, asset fetching, ffmpeg builders, and
youtube_uploader.upload_video. Run a temporary stage through pipeline.run_stage.
Assert:

~~~python
assert state["complete"] is True
assert state["script_provider"] == "anthropic"
assert state["quality"]["passed"] is True
assert state["youtube_id"] == "mock-video-id"
upload_video.assert_called_once()
~~~

Add a failed-quality case asserting that upload is not called and completion
remains false.

- [ ] **Step 2: Run the mocked smoke test without network access.**

~~~bash
python -m unittest tests.test_automation_smoke -v
~~~

Expected: pass without API keys, ffmpeg, or YouTube OAuth.

- [ ] **Step 3: Document operations in README.**

Document that cron-job.org triggers two independent IST slots, Claude defaults
to OpenAI fallback, random and round_robin are optional routing modes, and new
jobs must pass the local quality gate before upload. State explicitly that the
current phase does not change Pexels or add AI image generation.

- [ ] **Step 4: Run final verification.**

~~~bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
git diff --check
git status --short
~~~

Expected: all tests pass, compilation exits 0, diff check emits no output, and
only intended files are modified.

- [ ] **Step 5: Run a no-network dry-run smoke check.**

~~~bash
python -m src.main --format short --dry-run --topic "A test history topic"
~~~

Expected: script-only behavior; no Pexels, image generation, ffmpeg rendering,
or YouTube upload. If credentials are absent, the command must fail with a
clear provider credential error.

- [ ] **Step 6: Commit documentation and smoke tests.**

~~~bash
git add README.md tests/test_automation_smoke.py
git commit -m "test: verify automated publishing quality gate"
~~~

## Self-Review Checklist

- Daily jobs and two distinct topics: Tasks 2 and 4.
- Provider fallback, random, and round-robin: Task 1.
- Quality gate and upload blocking: Task 3.
- Resume and no-duplicate behavior: Tasks 1 through 5.
- Mock-only network testing: Tasks 1, 3, and 5.
- Cron preservation: Task 4.
- Analytics and selective AI visual fallback: explicitly deferred.
- No placeholders or unspecified implementation steps remain.
