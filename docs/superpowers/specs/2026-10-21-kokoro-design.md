# Kokoro narration for Histold

Approved direction: the user selected the Kokoro Michael sample and requires production narration to run entirely on GitHub Actions while their Mac is off.

Use Kokoro 0.9.4 with am_michael, American English, speed 0.95 and CPU inference. Run its dependencies in an isolated Python environment. A subprocess receives narration and returns audio plus native token-duration timestamps. Preserve the existing MP3 Voiceover contract. Validate exact normalized text coverage and timestamp bounds before accepting audio. A failed import, download, timeout, synthesis or invalid timing falls back to the existing Edge provider with a visible log entry. Retain strict timing validation for either provider.

The publish workflow provisions CPU PyTorch, Kokoro and the English spaCy model, caches Hugging Face model files, and sets the worker interpreter. Setup failure must leave Edge available. A manual smoke workflow generates only audio and timing artifacts on Ubuntu, with no publishing credentials or upload calls. No separate historical verification stage is added.

Validation: unit tests for routing/fallback, invalid output and token offset handling; actual narration through the production adapter; existing regression tests; manual GitHub Actions audio-only smoke run. GPU, voice cloning and Chatterbox are excluded.
