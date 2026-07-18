# Astrotold English-only audio and captions

## Purpose

Change Astrotold from Hinglish to English-only after the local dry run showed that the Hinglish audio was not acceptable.

## Configuration

Astrotold scripts and captions will be written in English. Edge TTS will use the existing English voice, en-US-GuyNeural. Its topic niche, astrology/numerology safety guidance, output isolation, channel lock, and two daily slots remain unchanged.

## Verification

A configuration regression test will assert the English language setting, English prompt direction, and en-US-GuyNeural voice. Existing channel setup tests must still pass. No OAuth, upload, or scheduler action is part of this change.

