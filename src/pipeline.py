"""Checkpointed, resumable video pipeline.

The runtime environment (e.g. a sandboxed scheduler) may cap each invocation at a
few seconds and kill background processes. To survive that, the pipeline is split
into discrete, idempotent steps that persist their output to a stage directory and
record progress in state.json. A killed run can be resumed; a step-at-a-time mode
lets a scheduler complete the whole pipeline across several short calls.

Steps: script → voiceover → captions → assets → render → compose → quality → upload
Each step is skipped if already marked done in state.json. Stages created before
the quality step keep their original target_steps and remain resumable.
"""
from __future__ import annotations

import json
import copy
from datetime import date, datetime
from pathlib import Path

from .config import Config, base_dir, env
from . import topics

STEP_ORDER = [
    "script", "voiceover", "captions", "assets", "render", "compose",
    "quality", "thumbnail", "upload",
]
PRODUCTION_STEPS = ['research', 'script', 'voiceover', 'align', 'audio_check',
                    'captions', 'assets', 'render', 'compose', 'quality', 'thumbnail', 'upload']


def stage_config(cfg: Config, st: dict) -> Config:
    if st.get('production_config'):
        return Config(copy.deepcopy(st['production_config']))
    data = copy.deepcopy(cfg.raw)
    data.setdefault('production', {})['enabled'] = False
    data['production']['strict_timing'] = False
    data.setdefault('video', {}).setdefault('branding', {})['enabled'] = False
    if data.get('script', {}).get('story_profile') == 'consequence_first':
        data['script'].pop('story_profile')
    return Config(data)


# --------------------------------------------------------------------------- #
# stage + state helpers
# --------------------------------------------------------------------------- #
def _slug(text: str, n: int = 40) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text)
    return "_".join(keep.lower().split())[:n] or "video"


def _out_root(cfg: Config) -> Path:
    return base_dir() / cfg.get("output.dir", "output")


def new_stage(
    cfg: Config,
    fmt: str,
    *,
    no_upload: bool,
    dry_run: bool,
    topic: str | None = None,
    privacy: str | None = None,
) -> Path:
    # Scheduled jobs are serialized; local callers also need atomic slot reservation.
    import fcntl
    root = _out_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.stage-creation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _new_stage(cfg, fmt, no_upload=no_upload, dry_run=dry_run, topic=topic, privacy=privacy)


def _new_stage(cfg: Config, fmt: str, *, no_upload: bool, dry_run: bool,
               topic: str | None = None, privacy: str | None = None) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = _out_root(cfg) / f"{ts}_{fmt}"
    stage.mkdir(parents=True, exist_ok=False)
    target = ["script"] if dry_run else list(STEP_ORDER)
    state = {
        "fmt": fmt,
        "visual_mode": _effective_mode(cfg, fmt),
        "created": ts,
        "job_id": f"{ts}_{fmt}",
        "slot": env("PUBLISH_SLOT", "manual"),
        "target_steps": target,
        "done": [],
        "complete": False,
        "error": None,
        "no_upload": no_upload,
        "overrides": {"topic": topic, "privacy": privacy},
    }
    if cfg.get('production.enabled', False) and (fmt == 'short' or state['visual_mode'] == 'cinematic_2d'):
        state['pipeline_version'] = 2
        state['production_config'] = copy.deepcopy(cfg.raw)
        if fmt == 'long':
            # Give the weekly script the direct response budget instead of letting
            # adaptive thinking exhaust it before any script JSON is emitted.
            state['production_config'].setdefault('script', {})['anthropic_effort'] = ''
        state['target_steps'] = ['research', 'script'] if dry_run else list(PRODUCTION_STEPS)
        rotate = cfg.get('editorial.rotate_design', False)
        auto_editorial = fmt == 'short' and cfg.get('editorial.enabled', False) and not topic
        if rotate or auto_editorial:
            index_path = base_dir() / 'data/published_index.json'
            entries = json.loads(index_path.read_text()) if index_path.exists() else []
        if rotate:
            from .editorial import DESIGNS, design
            latest = next((entry['design'] for entry in reversed(entries) if entry.get('design')), None)
            current = next((i for i, treatment in enumerate(DESIGNS) if treatment[0] == latest), -1)
            state['design'] = design(current + 1)
        if auto_editorial:
            from .editorial import assignment
            for previous in _out_root(cfg).glob('*/state.json'):
                try:
                    entries.append(json.loads(previous.read_text()))
                except (OSError, ValueError):
                    continue
            occupied = [e['editorial_slot'] for e in entries if isinstance(e, dict)
                        and isinstance(e.get('editorial_slot'), int) and not e.get('editorial_preview')]
            ordinal = max(occupied, default=-1) + 1
            from .strategy import load as load_strategy
            editorial = assignment(cfg, ordinal, load_strategy(base_dir()))
            from .editorial import this_week
            weekly = None if editorial.get('growth_trial') else this_week(cfg, date.today(), state['slot'], entries)
            if weekly:
                editorial.update(weekly)
                state['this_week_event'] = weekly['this_week'].get('event')
            state.update(editorial=editorial, editorial_slot=ordinal, editorial_preview=dry_run,
                         editorial_region=editorial['region']['id'], editorial_series=editorial['series']['name'],
                         duration_variant=editorial['duration_variant'], category=editorial['series']['id'])
            snapshot = state['production_config']
            snapshot.setdefault('script', {}).update(shorts_target_seconds=editorial['target_seconds'],
                                                    story_profile=editorial['story_profile'], enforce_beats=True)
            snapshot.setdefault('production', {})['editorial'] = editorial
            if editorial.get('growth_trial'):
                state['growth_trial']=editorial['growth_trial']
                snapshot['script']['title_style']='statement'
                state.update(experiment=editorial['growth_trial']['id'],experiment_slot=editorial['growth_trial']['index']+1)
            elif ordinal < cfg.get('editorial.experiment_posts', 56):
                state.update(experiment=cfg.get('editorial.experiment_id', 'facebook-world-history-4w'),
                             experiment_slot=ordinal + 1)
            arm = None if editorial.get('growth_trial') else ab_arm(cfg, ordinal)
            if arm:
                state.update(ab_test=arm)
                snapshot.setdefault('script', {})['title_style'] = arm['title_style']
                snapshot.setdefault('tts', {})['provider'] = arm['voice']
            from . import creative
            guidance = creative.load(base_dir())
            creative_arm = None if arm or editorial.get('growth_trial') else creative.arm(guidance, ordinal)
            if creative_arm:
                state.update(creative_test={'id': creative_arm['id'], 'arm': creative_arm['arm']})
            editorial['creative_guidance'] = [] if editorial.get('growth_trial') else creative.prompt_instructions(guidance, creative_arm)
        elif cfg.get('production.experiment', False):
            from .experiments import select_slot
            manifest_path = base_dir() / 'data/experiments/histold-six-reels.json'
            manifest = json.loads(manifest_path.read_text())
            index_path = base_dir() / 'data/published_index.json'
            entries = json.loads(index_path.read_text()) if index_path.exists() else []
            # In-flight stages reserve experiment slots as well as published posts.
            for previous in _out_root(cfg).glob('*/state.json'):
                try:
                    old = json.loads(previous.read_text())
                except (OSError, ValueError):
                    continue
                if isinstance(old, dict) and old.get('experiment') and not old.get('complete'):
                    entries.append(old)
            slot = select_slot(entries, manifest)
            if slot:
                state.update(experiment=manifest['id'], experiment_slot=slot['slot'], category=slot['category'])
                state['experiment_hook'] = slot['hook']
    if state.get('design'):
        state['production_config'].setdefault('production', {})['design'] = state['design']
    save_state(stage, state)
    return stage


def ab_arm(cfg: Config, ordinal: int) -> dict | None:
    """2x2 title-style x voice arm for editorial slot `ordinal`, or None outside the test window.

    Arms rotate every post (title) and every two posts (voice), so each block of
    four consecutive Shorts covers all four cells.
    """
    test = cfg.get('ab_test') or {}
    start = test.get('start_slot')
    if not test.get('enabled') or not isinstance(start, int) or not start <= ordinal < start + test.get('posts', 48):
        return None
    i = ordinal - start
    return {'id': test.get('id', 'voice-title-2x2'), 'index': i,
            'title_style': ('question', 'statement')[i % 2], 'voice': ('kokoro', 'edge')[(i // 2) % 2]}


def stage_path(cfg: Config, name: str) -> Path:
    """Resolve an explicit stage directory by name (bypasses active-stage lookup)."""
    return _out_root(cfg) / name


def state_path(stage: Path) -> Path:
    return stage / "state.json"


def load_state(stage: Path) -> dict:
    return json.loads(state_path(stage).read_text(encoding="utf-8"))


def save_state(stage: Path, state: dict) -> None:
    temporary = stage / 'state.json.tmp'
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(state_path(stage))


def active_stage(cfg: Config, fmt: str | None = None) -> Path | None:
    """Newest stage that has not completed and has no fatal error."""
    root = _out_root(cfg)
    if not root.exists():
        return None
    candidates = []
    for d in root.iterdir():
        sp = d / "state.json"
        if not sp.exists():
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if st.get("complete"):
            continue
        if fmt and st.get("fmt") != fmt:
            continue
        candidates.append((st.get("created", d.name), d))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


# --------------------------------------------------------------------------- #
# step implementations  (each takes cfg, stage, state and mutates state)
# --------------------------------------------------------------------------- #
def _rebuild_segments(state: dict):
    from .script_generator import Segment

    # The beat tag must survive the round-trip: the assets step uses it to
    # decide which segments get a generated illustration instead of stock.
    return [
        Segment(s["narration"], s.get("keywords", []), s.get("beat", ""), s.get('claim_ids', []))
        for s in state["script"]["segments"]
    ]


def _rebuild_assets(stage: Path, state: dict):
    from .assets import Asset

    stored = state.get("assets", [])
    if not stored:
        return []

    # New shape: one list of sub-clips per narration segment.
    if isinstance(stored[0], list) and stored[0] and isinstance(stored[0][0], list):
        return [
            [Asset(stage / p, is_video, []) for p, is_video in segment_assets]
            for segment_assets in stored
        ]

    # Backward compatibility for in-progress stages created before fast cuts.
    return [[Asset(stage / p, is_video, [])] for p, is_video in stored]


def _effective_mode(cfg: Config, fmt: str, st: dict | None = None) -> str:
    """Resolve the renderer, honoring the checkpoint for existing stages."""
    if st is not None and st.get("visual_mode"):
        return st["visual_mode"]
    if fmt != "short":
        return cfg.get("video.long_mode", "broll")
    # Only new stages opt into 3D; old checkpoints keep their original behavior.
    toggle = env("VIDEO_3D")
    enabled = (toggle.lower() in ("on", "true", "1")) if toggle is not None else cfg.get("video.three_d.enabled", False)
    if st is None and cfg.get("video.three_d.enabled", False) and enabled and not env("VIDEO_MODE"):
        return "three_motion"
    mode = env("VIDEO_MODE") or cfg.get("video.mode", "broll")
    return mode if mode in ("broll", "mascot", "captions_only", "motion_graphics",
                            "stopmotion", "claude_motion", "cinematic_2d", "three_motion") else "broll"


def _step_script(cfg: Config, stage: Path, st: dict) -> None:
    from .script_generator import generate_script

    overrides = st.get("overrides", {})
    if st.get('research'):
        cfg = Config(copy.deepcopy(cfg.raw))
        cfg.raw.setdefault('production', {})['research_context'] = st['research']
        cfg.raw['production']['category'] = st.get('category', '')
        if st.get('editorial'):
            cfg.raw['production']['editorial'] = st['editorial']
    script = generate_script(cfg, st["fmt"], topic_override=overrides.get("topic") or st.get('research', {}).get('topic'))
    st["script"] = script.to_dict()
    st["title"] = script.title
    st["script_provider"] = script.provider
    st["script_fallback_used"] = script.fallback_used
    # Countdown days produce distinct-by-design titles that read as near-dupes
    # to the fingerprint check, so skip uniqueness enforcement for them. A manual
    # --topic override wins over the countdown (mirroring generate_script), so its
    # title still gets normal dedup protection.
    is_countdown = (
        not overrides.get("topic")
        and topics.monthly_prediction_direction(cfg, slot=st.get("slot")) is not None
    )

    st["topic_reservation"] = topics.reserve_topic(
        script.title,
        st["fmt"],
        st.get("slot", "manual"),
        st.get("job_id") or stage.name,
        enforce_unique=not is_countdown,
    )
    (stage / "script.json").write_text(json.dumps(script.to_dict(), indent=2), encoding="utf-8")
    (stage / "metadata.json").write_text(
        json.dumps(
            {
                "title": script.title,
                "description": script.description,
                "tags": script.tags,
                "format": st["fmt"],
                "privacy_status": overrides.get("privacy") or cfg.get("youtube.privacy_status", "private"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_report(stage: Path, name: str, report: dict) -> None:
    (stage / f'{name}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def _step_research(cfg: Config, stage: Path, st: dict) -> None:
    from .research import research_topic, begin_step
    topic = st.get('overrides', {}).get('topic') or (
        f"{st['category']}: {st.get('experiment_hook', '')}" if st.get('category') else topics.pick_angle(cfg))
    leads = []
    if st.get('editorial') and not st.get('overrides', {}).get('topic'):
        from .editorial import brief
        topic = brief(st['editorial'], topics.recent_titles())
        leads = cfg.get('research.regional_source_leads', {}).get(st['editorial_region'], [])
        if st.get('growth_trial'):
            leads=cfg.get('research.growth_source_leads') or leads
    longform = st.get("fmt") == "long"
    if longform:
        evidence_brief = ("Choose a recognizable historical subject or a documented twist with direct evidence for the "
                          "opening and payoff. Choose your final angle only after inspecting the retrieved evidence. "
                          "Prefer a decision, object or institution whose explanation works through timelines, people "
                          "and documents without depending on an exact map. Drop disputed anecdotes and details that "
                          "are unnecessary to the final story.")
        topic += '\n' + evidence_brief
        cfg = Config(copy.deepcopy(cfg.raw))
        cfg.raw.setdefault('research', {})['longform'] = True
    # Fetched leads go to the model as page text (free); only unfetchable ones become search hints.
    if not begin_step(cfg, leads) and leads:
        topic += ('\nStart by searching these approved museum collection pages for a narrow, supported story. '
                  'They are research leads, not facts to copy without retrieval:\n' + '\n'.join(leads))
    report = research_topic(cfg, topic)
    _write_report(stage, 'research_attempt_1', report)
    if st.get('growth_trial'):
        # Growth-trial stories stop on unsupported research, so give them one
        # targeted recovery attempt (the errors are passed back) before failing a slot.
        retries = int(cfg.get('research.growth_retry_attempts', 1))
        for attempt in range(2, 2 + max(0, retries)):
            if report.get('status') == 'supported':
                break
            reason = report.get('error') or '; '.join(report.get('errors', [])) or report.get('status')
            print(f'  ! research attempt {attempt - 1} not supported ({reason}); retrying with feedback')
            previous = report
            blocked_core = any(f.get('material_to_core_story') is True and f.get('status') != 'supported'
                               for f in previous.get('findings', []) if isinstance(f, dict))
            if blocked_core and not st.get('overrides', {}).get('topic'):
                # A different story needs a fresh evidence packet. Never carry unrelated
                # contradictions into it, or erase them from the archived failed packet.
                rejected = previous.get('editorial_selection', {})
                fresh_topic = (topic + '\nThe previous story could not be established. Select a DISTINCT '
                               'story from the approved retrieved pages; do not reuse any rejected premise. '
                               'Return a complete independent evidence packet and three fresh candidates. '
                               'Rejected candidates (context only, not evidence): ' + json.dumps(rejected))
                report = research_topic(cfg, fresh_topic)
                st['research_reselection'] = {'attempt': attempt, 'reason': reason,
                                             'rejected_selection': copy.deepcopy(rejected)}
            else:
                report = research_topic(cfg, topic, recovery_request=previous)
            if not blocked_core and not report.get('editorial_selection') and previous.get('editorial_selection'):
                # Same-topic recovery keeps the premise it was asked to repair.
                report['editorial_selection'] = copy.deepcopy(previous['editorial_selection'])
            _write_report(stage, f'research_attempt_{attempt}', report)
    st['research'] = report
    _write_report(stage, 'research', report)
    if st.get('growth_trial'):
        from .growth_trial import validate_selection
        selection=validate_selection(report)
        st['editorial_selection']=selection
        if report.get('status')!='supported':
            raise ValueError('Growth trial story needs supported research before script and rendering')
    if report.get('status') != 'supported':
        details = report.get('error') or '; '.join(report.get('errors', [])) or report.get('status')
        print(f'  ! research not fully verified ({details}); continuing — see research.json')
    from .map_assets import required_maps
    st['maps'] = [{k: v for k, v in asset.items() if k != 'svg'} for asset in required_maps(st['research'])]


def _step_align(cfg: Config, stage: Path, st: dict) -> None:
    from .alignment import build_storyboard
    from .tts import WordTiming
    vo = st['voiceover']
    args = (st['script']['segments'], [WordTiming(*w) for w in vo['words']],
            vo['duration'], int(cfg.get(f"video.{st['fmt']}.fps", 30)), vo.get('timing_source', 'estimated'))
    try:
        st['storyboard'] = build_storyboard(*args, strict=True)
    except ValueError as exc:
        args = _realign(cfg, stage, st, args, exc)
        try:
            st['storyboard'] = build_storyboard(*args, strict=True)
        except ValueError as exc:
            print(f'  ! strict alignment failed ({exc}); using approximate timing')
            st['storyboard'] = build_storyboard(*args, strict=False)
    from .quality_gate import file_hash
    st['storyboard']['audio_sha256'] = file_hash(stage / vo['path'])
    _write_report(stage, 'storyboard', st['storyboard'])


def _realign(cfg: Config, stage: Path, st: dict, args: tuple, exc: ValueError) -> tuple:
    """Per-scene timing failed; force-align the script once before approximating."""
    from .tts import ALIGNERS, Voiceover, WordTiming, align_voiceover
    vo = st['voiceover']
    if cfg.get('tts.alignment_provider', '') not in ALIGNERS or vo.get('timing_source') == 'forced_aligned':
        return args
    print(f'  ! scene timing failed ({exc}); force-aligning the narration')
    text = ' '.join(s['narration'].strip() for s in st['script']['segments']).strip()
    try:
        aligned = align_voiceover(Voiceover(stage / vo['path'], vo['duration'], list(args[1]),
                                            vo.get('timing_source', 'estimated')), cfg, text)
    except Exception as err:
        print(f'  ! forced alignment unavailable ({type(err).__name__}: {err})')
        return args
    vo['words'] = [[w.word, w.start, w.end] for w in aligned.words]
    vo['timing_source'] = aligned.timing_source
    return (args[0], [WordTiming(*w) for w in vo['words']], *args[2:4], aligned.timing_source)


def _step_audio_check(cfg: Config, stage: Path, st: dict) -> None:
    from .audio_quality import analyze_audio
    st['audio_quality'] = analyze_audio(cfg, stage / st['voiceover']['path'], st['storyboard'])
    st['audio_quality']['audio_sha256'] = st['storyboard']['audio_sha256']
    _write_report(stage, 'audio_quality', st['audio_quality'])
    if st['audio_quality'].get('errors'):
        print('  ! narration audio flagged; continuing — see audio_quality.json')


def _step_voiceover(cfg: Config, stage: Path, st: dict) -> None:
    from .tts import synthesize
    from .script_generator import Script

    segs = _rebuild_segments(st)
    narration = " ".join(s.narration.strip() for s in segs).strip()
    vo = synthesize(cfg, narration, stage / "voiceover.mp3")
    st["voiceover"] = {
        "path": "voiceover.mp3",
        "duration": vo.duration,
        "words": [[w.word, w.start, w.end] for w in vo.words],
        "timing_source": vo.timing_source,
        "provider": vo.provider,
    }


def _step_captions(cfg: Config, stage: Path, st: dict) -> None:
    if not cfg.get("video.captions.enabled", True):
        st["captions"] = None
        return
    from .captions import build_ass
    from .tts import WordTiming

    words = [WordTiming(*w) for w in st["voiceover"]["words"]]
    mode = _effective_mode(cfg, st["fmt"], st)
    position = {"mascot": "top", "captions_only": "center",
                "motion_graphics": "center"}.get(mode, "bottom")
    if mode in ("three_motion", "cinematic_2d"):
        data = copy.deepcopy(cfg.raw)
        data.setdefault("video", {}).setdefault("captions", {}).update(
            cfg.get("video.cinematic_2d.captions", {"fontsize_short": 66, "outline": 2, "margin_bottom": 180}) if mode == "cinematic_2d" else cfg.get("video.three_d.captions", {"fontsize_short": 58, "outline": 3})
        )
        cfg = Config(data)
    build_ass(
        cfg,
        words,
        stage / "captions.ass",
        st["fmt"],
        position=position,
        words_per_caption=cfg.get("video.captions.words_per_beat", 4),
    )
    st["captions"] = "captions.ass"


def _step_assets(cfg: Config, stage: Path, st: dict) -> None:
    # captions_only / motion_graphics need no footage; claude_motion fetches
    # stock only for segments whose scene fails, at render time.
    if _effective_mode(cfg, st["fmt"], st) in ("captions_only", "motion_graphics", "claude_motion", "cinematic_2d", "three_motion"):
        st["assets"] = []
        return
    from .assets import fetch_for_segments

    segs = _rebuild_segments(st)
    assets = fetch_for_segments(cfg, segs, stage, st["fmt"])
    st["assets"] = [
        [[str(Path(a.path).relative_to(stage)), a.is_video] for a in segment_assets]
        for segment_assets in assets
    ]


def _step_render(cfg: Config, stage: Path, st: dict) -> None:
    """Build the silent visuals. broll → silent.mp4; mascot → bg.mp4 + owl.mov."""
    from .tts import WordTiming
    from . import video_builder as vb

    duration = st["voiceover"]["duration"]
    assets = _rebuild_assets(stage, st)
    words = [WordTiming(*w) for w in st["voiceover"]["words"]]
    mode = _effective_mode(cfg, st["fmt"], st)

    if mode == "mascot":
        from .mascot import render_overlay

        vb.build_broll_silent(cfg, assets, duration, words, st["fmt"], stage, "bg.mp4")
        mascot_dir = base_dir() / cfg.get("video.mascot.dir", "Mascot")
        render_overlay(cfg, stage / "voiceover.mp3", duration, st["fmt"],
                       stage / "owl.mov", mascot_dir)
    elif mode == "captions_only":
        from .mascot import render_background

        render_background(cfg, duration, st["fmt"], stage / "silent.mp4")
    elif mode == "motion_graphics":
        from .motion import render_motion_video

        render_motion_video(cfg, duration, st["fmt"], stage / "silent.mp4")
    elif mode == "three_motion":
        from .three_motion import render_motion_video as render_three_motion

        try:
            render_three_motion(cfg, st, stage, words, duration)
        except Exception as exc:  # noqa: BLE001 - a 3D failure must not cost the daily slot
            fallback = cfg.get("video.mode", "claude_motion")
            if fallback not in ("claude_motion", "cinematic_2d"):
                fallback = "claude_motion"
            print(f"  ! 3D render failed ({exc}); falling back to {fallback}")
            st["render_fallback"] = str(exc)
            st["visual_mode"] = fallback
            if fallback == "cinematic_2d":
                from .cinematic_motion import render_motion_video as render_2d
            else:
                from .claude_motion import render_motion_video as render_2d
            render_2d(cfg, st, stage, words, duration)
    elif mode == "cinematic_2d":
        from .cinematic_motion import render_motion_video as render_cinematic

        render_cinematic(cfg, st, stage, words, duration)
    elif mode == "claude_motion":
        from .claude_motion import render_motion_video as render_claude_motion

        render_claude_motion(cfg, st, stage, words, duration)
    else:
        vb.build_broll_silent(cfg, assets, duration, words, st["fmt"], stage, "silent.mp4")


def _step_compose(cfg: Config, stage: Path, st: dict) -> None:
    """Add captions + audio. mascot → also overlay the owl onto the b-roll."""
    from . import video_builder as vb

    ass = (stage / st["captions"]) if st.get("captions") else None
    vo = stage / "voiceover.mp3"
    out = stage / "video.mp4"

    if st.get("cinematic_effects"):
        data = copy.deepcopy(cfg.raw)
        data.setdefault("video", {})["effects_file"] = str((stage / st["cinematic_effects"]).resolve())
        cfg = Config(data)

    if _effective_mode(cfg, st["fmt"], st) == "mascot":
        vb.compose_overlay(cfg, stage / "bg.mp4", stage / "owl.mov", vo, ass, out, stage, base_dir())
    else:
        vb.finalize(cfg, stage / "silent.mp4", vo, ass, out, stage, base_dir())
    st["video"] = "video.mp4"


def _step_quality(cfg: Config, stage: Path, st: dict) -> None:
    from .quality_gate import validate_stage

    strict_visuals = st.get("visual_mode") in ("three_motion", "cinematic_2d")
    if not strict_visuals and not cfg.get("quality.enabled", True) and not cfg.get('production.enabled', False):
        st["quality"] = {"passed": True, "skipped": True, "score": 100, "checks": {}, "errors": []}
        return
    report = validate_stage(cfg, stage, st)
    st["quality"] = report
    (stage / "quality.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if not report["passed"]:
        failed = ", ".join(
            name for name, status in report["checks"].items() if status == "fail"
        )
        # Modern renderers are strict on visual checks; research/content keep the usual warn-only rule.
        visual = {"render_quality", "video", "dimensions", "duration", "composition_binding",
                  "caption_composition", "branding"}
        bad_visual = [n for n, v in report["checks"].items() if v == "fail" and n in visual]
        if strict_visuals and bad_visual:
            raise RuntimeError(f"Renderer visual checks failed: {', '.join(bad_visual)}; see quality.json")
        # Warn only: the video is already paid for, so it publishes anyway.
        print(f"  ! quality checks flagged: {failed}; continuing — see quality.json")


def _step_thumbnail(cfg: Config, stage: Path, st: dict) -> None:
    if not cfg.get("thumbnails.enabled", False):
        st["thumbnail"] = None
        return
    from .thumbnail import make_thumbnail

    st["thumbnail"] = make_thumbnail(cfg, st, stage)


def _closing_question(sc: dict) -> str:
    """Last question the narration asks (the callback), reused as the IG comment prompt."""
    import re
    found = re.findall(r'[^.!?]*\?', ((sc.get('segments') or [{}])[-1]).get('narration', ''))
    return found[-1].strip() if found else ''


def _step_upload(cfg: Config, stage: Path, st: dict) -> None:
    sc = st["script"]
    if st.get('credits'):  # licence attribution for library art used in the video (CC BY)
        sc = {**sc, 'description': sc['description'].rstrip() + '\n\n' + '\n'.join(st['credits'])}
    facebook_primary = (st.get('fmt') == 'short' and cfg.get('meta.primary', False))

    def save_meta(results: dict) -> None:
        # Persist each platform ID as it lands so a crash/retry never re-posts.
        st['meta'] = results
        save_state(stage, st)
    if facebook_primary:
        if not cfg.get('meta.enabled') or not cfg.get('meta.post_to_facebook') or not cfg.get('meta.page_id'):
            raise RuntimeError('Facebook primary publishing requires Meta enabled and a Facebook Page')
        from .meta_uploader import publish_reel
        # Idempotent: skips platforms already in st['meta'], retries the rest.
        st['meta'] = publish_reel(cfg, stage / 'video.mp4', sc['title'], sc['description'],
                                  existing_results=st.get('meta'), on_progress=save_meta,
                                  question=_closing_question(sc))
        if not st.get('meta', {}).get('facebook'):
            raise RuntimeError('Facebook primary upload failed: ' + st.get('meta', {}).get('facebook_error', 'No Reel ID returned'))
        _record_published(cfg, stage, st)

    if cfg.get("youtube.enabled", False) and not st.get('youtube_id'):
        from .youtube_uploader import upload_video

        privacy_override = st.get("overrides", {}).get("privacy")
        vid = upload_video(
            cfg, stage / "video.mp4", sc["title"], sc["description"], sc["tags"],
            thumbnail_path=stage / st["thumbnail"] if st.get("thumbnail") else None,
            privacy_override=privacy_override,
        )
        st["youtube_id"] = vid
        st["youtube_url"] = f"https://youtu.be/{vid}"
        save_state(stage, st)
        _record_published(cfg, stage, st)
    elif not cfg.get('youtube.enabled', False):
        st["upload_skipped"] = "youtube.enabled=false"

    # Cross-post to Facebook/Instagram Reels. Only shorts (vertical) qualify, and
    # this is best-effort: a Meta failure must never fail the run or block the
    # primary YouTube upload. Every short goes out when meta.enabled is on.
    if cfg.get("meta.enabled", False) and st.get("fmt") == "short" and not facebook_primary:
        from .meta_uploader import publish_reel

        try:
            st["meta"] = publish_reel(cfg, stage / "video.mp4", sc["title"], sc["description"],
                                      existing_results=st.get("meta"), on_progress=save_meta,
                                      question=_closing_question(sc))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Meta cross-post skipped: {exc}")
            st["meta_error"] = str(exc)


STEP_FUNCS = {
    'research': _step_research,
    'align': _step_align,
    'audio_check': _step_audio_check,
    "script": _step_script,
    "voiceover": _step_voiceover,
    "captions": _step_captions,
    "assets": _step_assets,
    "render": _step_render,
    "compose": _step_compose,
    "quality": _step_quality,
    "thumbnail": _step_thumbnail,
    "upload": _step_upload,
}


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def _skip_upload(st: dict) -> bool:
    return bool(st.get("no_upload")) and "upload" not in st.get("done", [])


def _pending_steps(st: dict) -> list[str]:
    skip = _skip_upload(st)
    return [s for s in st["target_steps"] if s != 'verify' and s not in st["done"] and not (s == "upload" and skip)]


def _is_satisfied(st: dict) -> bool:
    skip = _skip_upload(st)
    return all(s in st["done"] for s in st["target_steps"] if s != 'verify' and not (s == "upload" and skip))


def run_stage(
    cfg: Config,
    stage: Path,
    *,
    one_step: bool,
    notifier=None,
    force_upload: bool = False,
    privacy_override: str | None = None,
) -> dict:
    """Execute pending steps for a stage. If one_step, do exactly one then return.

    force_upload reopens a stage that previously finished with no_upload=True
    (its "upload" step was skipped, not failed) so the upload step now runs —
    used by the approval-queue flow after a human approves a rendered draft.
    privacy_override lets that approval choose a privacy status different from
    whatever (if anything) was set when the draft was originally generated.
    Returns the (updated) state dict. Sends notifications on terminal events.
    """
    st = load_state(stage)
    cfg = stage_config(cfg, st)
    dirty = False
    if 'verify' in st['target_steps']:
        st['target_steps'] = [step for step in st['target_steps'] if step != 'verify']
        # The removed stage used to populate map metadata, including on resumed jobs.
        if st.get('research', {}).get('status') == 'supported':
            from .map_assets import required_maps
            st['maps'] = [{k: v for k, v in asset.items() if k != 'svg'} for asset in required_maps(st['research'])]
        dirty = True
    if force_upload and st.get("no_upload"):
        st["no_upload"] = False
        st["complete"] = False
        dirty = True
    if privacy_override:
        st.setdefault("overrides", {})["privacy"] = privacy_override
        dirty = True
    if dirty:
        save_state(stage, st)

    pending = _pending_steps(st)

    for step in pending:
        print(f"  → step: {step}")
        try:
            STEP_FUNCS[step](cfg, stage, st)
            st["done"].append(step)
            st["error"] = None
            save_state(stage, st)
        except Exception as exc:  # noqa: BLE001
            st["error"] = str(exc)
            save_state(stage, st)
            print(f"  !! step '{step}' failed: {exc}")
            if notifier is not None:
                notifier.notify_failure(st["fmt"], str(exc))
            raise
        if one_step:
            break

    # Completion handling
    if _is_satisfied(st) and not st.get("complete"):
        st["complete"] = True
        save_state(stage, st)
        if not st.get("topic_recorded"):
            topics.record_topic(st.get("title", "untitled"), st["fmt"])
            st["topic_recorded"] = True
            save_state(stage, st)
        if notifier is not None:
            notifier.notify_success(
                st["fmt"], st.get("title", ""), st.get("youtube_url")
            )
        print("  ✓ stage complete")
        _record_published(cfg, stage, st)
        _cleanup(cfg, stage, st)
    return st


def _record_published(cfg: Config, stage: Path, st: dict) -> None:
    """Append the uploaded video to data/published_index.json.

    Must run before _cleanup: cleanup deletes the stage dir, and this index is
    the only durable video_id → run/stage mapping (used by the daily cap and
    the dashboard retitle/repost feature). Best-effort: never fails the run.
    """
    if not st.get("youtube_id") and not st.get('meta', {}).get('facebook'):
        return
    try:
        from datetime import timezone

        path = base_dir() / "data" / "published_index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        existing = next((row for row in entries if row.get('stage_dir_name') == stage.name), None)
        record = {
            "video_id": st.get("youtube_id"),
            "run_id": env("PUBLISH_ARTIFACT_RUN_ID") or env("GITHUB_RUN_ID") or "",
            "stage_dir_name": stage.name,
            "title": st.get("title", ""),
            "published_at": existing['published_at'] if existing else datetime.now(timezone.utc).isoformat(),
            "channel": env("CHANNEL_LABEL") or "histold",
            "slot": st.get("slot") or env("PUBLISH_SLOT") or "manual",
            "scheduled": env("PUBLISH_SCHEDULED") == "true",
            "format": st.get("fmt", "short"),
            "publish_claim": env("PUBLISH_CLAIM"),
            "design": (st.get("design") or {}).get("name"),
            "workflow": env("PUBLISH_WORKFLOW") or "publish",
            "retitled_at": None,
            "republished_from": None,
            "republished_as": None,
            'experiment': st.get('experiment'),
            'experiment_slot': st.get('experiment_slot'),
            'ab_test': st.get('ab_test'),
            'creative_test': st.get('creative_test'),
            'growth_trial': st.get('growth_trial'),
            'editorial_selection': st.get('editorial_selection'),
            'voice': st.get('voiceover', {}).get('provider'),
            'category': st.get('category'),
            'editorial_slot': st.get('editorial_slot'),
            'editorial_region': st.get('editorial_region'),
            'editorial_series': st.get('editorial_series'),
            'this_week_event': st.get('this_week_event'),
            'duration_variant': st.get('duration_variant'),
            'hook': (st.get('script', {}).get('segments') or [{}])[0].get('narration'),
            'duration': st.get('voiceover', {}).get('duration'),
            'production_profile': st.get('pipeline_version', 1),
            'facebook_id': st.get('meta', {}).get('facebook'),
            'instagram_id': st.get('meta', {}).get('instagram'),
        }
        if existing:
            existing.update(record)
        else:
            entries.append(record)
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        print(f"  ✓ recorded {st.get('youtube_id') or st.get('meta', {}).get('facebook')} in {path.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! published-index record skipped: {exc}")


def _cleanup(cfg: Config, stage: Path, st: dict) -> None:
    """After a successful upload, delete this run's local files; also prune old runs."""
    import shutil

    preserve_artifact = env("PRESERVE_STAGE_ARTIFACT") == "true"
    if st.get("youtube_id") and cfg.get("output.delete_after_upload", False) and not preserve_artifact:
        shutil.rmtree(stage, ignore_errors=True)
        print(f"  🧹 removed local renders for {stage.name} (already on YouTube)")
    elif st.get("youtube_id") and preserve_artifact:
        print(f"  ✓ preserving {stage.name} until the workflow uploads its artifact")
    _prune_old_stages(cfg, keep=stage)


def _prune_old_stages(cfg: Config, keep: Path | None = None) -> None:
    """Delete leftover stage folders older than output.keep_days (failed/no-upload runs)."""
    import shutil
    import time

    root = _out_root(cfg)
    if not root.exists():
        return
    days = cfg.get("output.keep_days", 3)
    cutoff = time.time() - days * 86400
    for d in root.iterdir():
        # Only touch timestamped stage dirs (e.g. 20260628_144312_short); leave others alone.
        if not d.is_dir() or d == keep or not d.name[:8].isdigit():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except FileNotFoundError:
            pass


def remaining_steps(stage: Path) -> list[str]:
    st = load_state(stage)
    return _pending_steps(st)
