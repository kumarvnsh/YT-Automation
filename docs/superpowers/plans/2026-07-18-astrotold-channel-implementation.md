# Astrotold Channel Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add Astrotold as an isolated Hinglish astrology-and-numerology channel that publishes two Shorts per day using the current pipeline.

**Architecture:** Generalize the script prompt from optional channel configuration. Create separate Astrotold configuration, local runner, and cloud workflow paths so credentials and video state remain isolated from Histold.

**Tech Stack:** Python 3.10+, PyYAML, unittest, Bash, GitHub Actions, Edge TTS, YouTube Data API v3.

---

### Task 1: Generalize script instructions by channel

Files:
- Modify: src/script_generator.py
- Create: tests/test_channel_setup.py

- [ ] Step 1: Add a test that creates a minimal Histold config and verifies the built prompt still contains the default history / did-you-know niche.
- [ ] Step 2: Add a second test using an Astrotold-style Config with channel.niche, channel.language_instruction, channel.content_rules, and channel.safety_rules. Patch the module date to 18 July 2026. Verify the prompt contains the configured astrology/numerology niche, TODAY'S DATE: 18 July 2026, Hinglish instruction, English visual keyword instruction, and entertainment/no-guarantees rule.
- [ ] Step 3: Run python -m unittest tests.test_channel_setup.ChannelPromptTests -v. Expect failure because prompt configuration and date fields are not yet implemented.
- [ ] Step 4: In _build_prompt, import date from datetime and read:
  - channel.niche, defaulting to history / did-you-know
  - channel.language_instruction, defaulting to empty
  - channel.content_rules, defaulting to empty
  - channel.safety_rules, defaulting to empty.
  Format the current date as day, full month, and year. Replace only the fixed niche in the TASK line. Insert the date plus non-empty channel rules immediately below TASK. Retain all current duration, segment, title, tag, safety, and deduplication rules.
- [ ] Step 5: Run python -m unittest tests.test_channel_setup.ChannelPromptTests tests.test_script_provider_routing -v. Expect all tests to pass.
- [ ] Step 6: Commit src/script_generator.py and tests/test_channel_setup.py with message feat: configure script prompts per channel.

### Task 2: Add the isolated Astrotold configuration

Files:
- Create: channels/astrotold/config.yaml
- Create: channels/astrotold/README.md
- Modify: tests/test_channel_setup.py

- [ ] Step 1: Write a test that loads channels/astrotold/config.yaml and expects:
  - channel.name is Astrotold
  - channel.niche is astrology and numerology
  - tts.edge_voice is hi-IN-MadhurNeural
  - youtube.privacy_status is private
  - youtube.expected_channel_id is empty.
- [ ] Step 2: Run python -m unittest tests.test_channel_setup.AstrotoldConfigTests -v. Expect FileNotFoundError.
- [ ] Step 3: Create the configuration with:
  - channel.language: hinglish
  - Roman-script, conversational Hinglish direction
  - upbeat entertainment readings and English visual-search keywords
  - no guarantees and no medical, legal, financial, or emergency advice
  - topic angles for birth dates 6/15/24, monthly birth-number readings, name initials, lucky colour/number/day, current-month zodiac readings, and weekly zodiac guidance
  - 28-second Shorts, Claude with OpenAI fallback, and Edge TTS voice hi-IN-MadhurNeural at +4 percent
  - private YouTube uploads, category 22, blank expected channel ID, and Astro tags
  - output.dir set to `output` (because load_config() uses the config folder as
    base_dir, the workflow stage path remains channels/astrotold/output), no
    automatic delete, seven-day retention
  - safe root video, assets, quality, and notifications defaults
  - disabled Meta cross-posting without any Histold social IDs.
- [ ] Step 4: Create the guide with these commands:
  python scripts/setup_oauth.py channels/astrotold/config.yaml
  python scripts/whoami_youtube.py channels/astrotold/config.yaml
  python -m src.main --config channels/astrotold/config.yaml --format short --dry-run
  python -m src.main --config channels/astrotold/config.yaml --format short --no-upload
  Explain that credentials are kept in channels/astrotold/secrets, the channel ID must be copied to youtube.expected_channel_id, and two private uploads require review before going public.
- [ ] Step 5: Run python -m unittest tests.test_channel_setup -v. Expect pass without network calls.
- [ ] Step 6: Commit configuration, guide, and tests with message feat: add Astrotold channel configuration.

### Task 3: Add a reusable two-slot local runner

Files:
- Create: scripts/run_channel.sh
- Modify: channels/astrotold/README.md
- Modify: tests/test_channel_setup.py

- [ ] Step 1: Write a test that confirms the runner requires a config path and morning/evening slot, exports PUBLISH_SLOT, and invokes python -m src.main with config and short format.
- [ ] Step 2: Run python -m unittest tests.test_channel_setup.ChannelRunnerTests -v. Expect failure because the runner does not exist.
- [ ] Step 3: Create a strict Bash runner. Validate the slot is exactly morning or evening. Resolve the project root, set scheduler-safe PATH, activate .venv if available, export PUBLISH_SLOT, and run the existing main module with the config path and short format. Mark it executable.
- [ ] Step 4: Add exact 09:00 and 18:00 local cron examples in the channel guide, calling scripts/run_channel.sh with the Astrotold config and morning/evening respectively.
- [ ] Step 5: Run python -m unittest tests.test_channel_setup.ChannelRunnerTests -v. Expect pass.
- [ ] Step 6: Commit runner, guide, and test with message feat: add channel-aware daily runner.

### Task 4: Add isolated cloud publishing

Files:
- Create: .github/workflows/publish-astrotold.yml
- Modify: tests/test_workflows.py

- [ ] Step 1: Add a regression test that checks the new workflow contains the Astrotold config path, ASTROTOLD_YT_CLIENT_SECRET_JSON_B64, ASTROTOLD_YT_TOKEN_JSON_B64, Astrotold secrets directory, CHANNEL_LABEL=astrotold, Astrotold topic reservation state path, and both morning/evening slot exports.
- [ ] Step 2: Run python -m unittest tests.test_workflows.WorkflowRegressionTests.test_astrotold_publisher_uses_isolated_config_and_secrets -v. Expect FileNotFoundError.
- [ ] Step 3: Create a dedicated workflow from publish.yml that retains checkout, Python, ffmpeg, dependency installation, pipeline step loop, IST slot calculation, and artifact upload. Change all channel values to Astrotold:
  - concurrency group publish-astrotold
  - OAuth files only in channels/astrotold/secrets
  - only ASTROTOLD_YT_CLIENT_SECRET_JSON_B64 and ASTROTOLD_YT_TOKEN_JSON_B64
  - CHANNEL_LABEL=astrotold
  - pipeline uses the Astrotold config
  - stages come from channels/astrotold/output
  - state commits only update Astrotold used topics, published index, and reservations.
  Exclude Histold Meta secrets, root data, approval queue, and auto-republish behavior. Keep dispatch inputs topic, privacy_status, dry_run, no_upload, and scheduled.
- [ ] Step 4: Run python -m unittest tests.test_workflows -v. Expect all Histold and Astrotold workflow checks to pass.
- [ ] Step 5: Commit workflow and test with message feat: add Astrotold publishing workflow.

### Task 5: Verify and document activation

Files:
- Modify: README.md

- [ ] Step 1: Add an Additional channels section linking to channels/astrotold/README.md and showing the config-based dry-run command.
- [ ] Step 2: Run python -m unittest discover -s tests -v. Expect all tests to pass.
- [ ] Step 3: If LLM credentials exist, run python -m src.main --config channels/astrotold/config.yaml --format short --dry-run. Confirm all artifacts are below channels/astrotold/output and no YouTube upload occurs. If the key is absent, report that exact key and do not add a placeholder.
- [ ] Step 4: Commit README with message docs: explain Astrotold channel usage.
- [ ] Step 5: After code verification, perform user-owned OAuth:
  python scripts/setup_oauth.py channels/astrotold/config.yaml
  python scripts/whoami_youtube.py channels/astrotold/config.yaml
  Copy the returned channel ID into the config, retain private visibility for two reviewed uploads, then set public visibility and add the two Astrotold OAuth secrets to GitHub Actions.

## Plan self-review

- Spec coverage: Tasks 1-2 cover configurable prompting, Hinglish, current date, prediction guardrails, Hindi TTS, and configuration isolation. Task 3 covers two local slots; Task 4 isolates cloud credentials and state; Task 5 verifies activation.
- Placeholder scan: Each task has concrete files, commands, configuration requirements, expected failure/pass states, and a commit.
- Type consistency: channel.language_instruction, channel.content_rules, and channel.safety_rules are consistent across source, tests, and configuration. Slots are always morning or evening.
