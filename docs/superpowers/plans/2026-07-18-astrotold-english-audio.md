# Astrotold English Audio Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task.

**Goal:** Change Astrotold scripts, captions, and TTS from Hinglish to English-only.

**Architecture:** This is a channel-only configuration change. The prompt reads the existing language instruction and Edge TTS reads the configured voice, so no pipeline code changes are required.

**Tech Stack:** Python, PyYAML, unittest.

---

### Task 1: Configure and verify English-only Astrotold output

Files:
- Modify: channels/astrotold/config.yaml
- Modify: tests/test_channel_setup.py

- [ ] Step 1: Extend AstrotoldConfigTests to assert channel.language is en, the language instruction explicitly requires English only, and tts.edge_voice is en-US-GuyNeural.
- [ ] Step 2: Run python -m unittest tests.test_channel_setup.AstrotoldConfigTests -v. Expect failure because current values are Hinglish and hi-IN-MadhurNeural.
- [ ] Step 3: In channels/astrotold/config.yaml change channel.language to en; replace Hinglish/Roman-script wording with an explicit English-only script and caption instruction; set tts.edge_voice to en-US-GuyNeural. Keep all niche, safety, upload, output, and scheduling values unchanged.
- [ ] Step 4: Run python -m unittest tests.test_channel_setup -v. Expect pass.
- [ ] Step 5: Commit only config and test with message feat: use English audio for Astrotold.

### Task 2: Update the channel operating guide

Files:
- Modify: channels/astrotold/README.md

- [ ] Step 1: Replace Hinglish references with English-only script, captions, and en-US-GuyNeural narration.
- [ ] Step 2: Run python -m unittest discover -s tests -v. Expect all tests pass.
- [ ] Step 3: Commit the guide with message docs: describe Astrotold English output.

## Plan self-review

- Scope: limited to Astrotold configuration, test, and guide; no uploader, OAuth, or scheduler behavior changes.
- Verification: targeted config test first, then complete suite.

