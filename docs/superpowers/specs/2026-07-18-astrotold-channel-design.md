# Astrotold channel setup

## Purpose

Launch Astrotold as a second, isolated YouTube channel that uses the existing
video-generation and upload pipeline for astrology and numerology Shorts. It
will publish two Hinglish Shorts each day without sharing content history,
OAuth credentials, output, or analytics with Histold.

## Channel configuration

Astrotold will live in `channels/astrotold/`. Its `config.yaml` will supply the
channel's niche, persona, recurring content directions, Hindi Edge TTS voice,
caption styling, YouTube metadata, and an initially blank
`youtube.expected_channel_id`. A local `.env` and `secrets/` directory will be
used only for Astrotold credentials. Generated media and state will remain
under that channel directory.

The default language is Hinglish written in Roman script. Scripts must use
natural Hindi with familiar English astrology vocabulary. Captions reproduce
the narration. The configured `hi-IN-MadhurNeural` Edge voice provides the
Hindi-oriented voiceover; the user may later select a different Hindi voice.

## Content plan

The channel produces one Short per publishing slot:

| Slot | Content | Examples |
| --- | --- | --- |
| Morning | Numerology | monthly guidance for birth dates 6/15/24; name-initial readings; lucky colour, number, and day |
| Evening | Astrology | monthly and weekly readings by zodiac sign; timely moon and planetary themes |

Each script receives today's date so monthly or daily readings refer to the
correct period. Topic selection rotates series directions and retains
per-channel deduplication so that the same sign, birth-number group, or hook
does not repeat too soon.

Predictions are entertainment and general guidance. Prompts must avoid
guarantees or specific medical, legal, or financial advice. Metadata will make
the entertainment framing clear. Image/video search keywords remain English to
work effectively with the existing stock-media provider.

## Shared-pipeline changes

The script prompt currently hard-codes the History/Did-you-know niche. It will
be generalized to read the channel niche and optional content/safety guidance
from the active configuration. Histold's current behavior must remain
unchanged when its root configuration is used.

The daily runner will accept a channel config path and slot argument, enabling
two independently scheduled Astrotold jobs. The cloud publishing workflow will
be parameterized or supplemented with an Astrotold workflow so its credentials
and channel data are never mixed with Histold's.

## YouTube connection and launch safety

The configuration will keep uploading disabled or private until OAuth is
completed. After the user creates/selects the Astrotold YouTube channel, they
will run OAuth against `channels/astrotold/config.yaml`, record the resulting
channel ID as `youtube.expected_channel_id`, and perform private test uploads
before public scheduling. Histold's existing token and expected-channel guard
will remain untouched.

## Verification

Automated tests will verify that the generalized script prompt keeps Histold's
history language while applying Astrotold's niche, Hinglish rules, timely date,
and prediction guardrails. Configuration tests will verify the Astrotold
channel uses isolated paths and the intended Hindi voice. A dry run using the
Astrotold configuration will confirm script generation and artifacts before
OAuth/upload is attempted.
