# Astrotold Numerology Niche Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task.

**Goal:** Make Astrotold a numerology-only English Shorts channel.

**Architecture:** Update only the Astrotold configuration and configuration regression test. The existing generic prompt reads the channel values, so no pipeline change is required.

**Tech Stack:** Python, PyYAML, unittest.

---

### Task 1: Configure and test the numerology-only niche

Files:
- Modify: channels/astrotold/config.yaml
- Modify: tests/test_channel_setup.py
- Modify: channels/astrotold/README.md

- [ ] Step 1: Write a failing configuration test that expects a numerology-only niche, no zodiac seed angle or tag, and birth-number/name-initial/lucky-value topic angles.
- [ ] Step 2: Run the focused config test and observe its expected failure.
- [ ] Step 3: Change the Astrotold niche, persona, content rules, seed angles, and default tags to numerology-only. Preserve English audio, channel lock, uploads, output, safety rules, and scheduling.
- [ ] Step 4: Update the guide’s channel description to numerology-only.
- [ ] Step 5: Run focused and full unittest discovery; commit configuration, test, and guide.

## Plan self-review

The scope excludes upload mechanics and shared pipeline code. Tests verify both removal of zodiac content and preservation of the safe existing channel setup.

