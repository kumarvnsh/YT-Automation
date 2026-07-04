# Channel Setup Guide — History / Did-You-Know

Everything to launch the channel. Branding is built around a **deep-navy + gold** "antiquity" look that matches the cinematic stock footage the pipeline pulls.

## 1. Channel name

Pick one. All four suit faceless, fact-driven history content and work for both Shorts and long-form. **Check handle availability** at `youtube.com/@yourhandle` before committing (also grab the matching TikTok/Instagram handle).

| Name | Handle idea | Why it works |
|---|---|---|
| **History Unearthed** ⭐ (recommended) | `@HistoryUnearthed` | "History" keyword helps search/discovery; "Unearthed" signals surprising, dug-up facts. Strong logo potential. |
| **Pocket History** | `@PocketHistory` | Friendly, implies bite-sized — perfect for Shorts. |
| **Histold** | `@Histold` | Coined word (History + told). Unique, brandable, short. |
| **The Curious Past** | `@TheCuriousPast` | Inquisitive, premium tone; great for longer videos. |

Recommended: **History Unearthed** — searchable, memorable, and the hourglass logo fits it cleanly.

## 2. Logo / avatar

File: `assets/branding/logo_avatar.svg` (vector — scales to any size with no quality loss).

- **Concept:** a gold hourglass in a navy badge with a thin gold ring. The hourglass = timelessness/history; reads clearly even at the tiny avatar size YouTube shows in comments.
- **Export to PNG** for upload (YouTube wants 800×800, min 98×98, PNG/JPG, under 4 MB). I can export it for you.
- Same mark works as a Shorts watermark and a video intro sting later.

## 3. Banner (channel art)

Spec: **2560×1440 px**, but keep critical content inside the **1546×423 "safe area"** (center) — that's all that shows on phones.

- Background: the same navy radial gradient.
- Center: the hourglass mark + channel name in a serif/elegant font, with a one-line tagline beneath.
- Tagline options: *"Surprising history, every day."* / *"The facts they left out of class."* / *"History's best-kept secrets."*
- I can generate the full banner PNG on request.

## 4. Visual identity (use everywhere)

- **Colors:** Navy `#16213E` / `#0D152B` (background), Gold `#E8B84B` (accent), White `#FFFFFF` (captions). These already match `config.yaml` caption + fallback colors.
- **Fonts:** Headings — a classic serif (Playfair Display, Cinzel). Captions/body — a bold sans (Montserrat, Arial). Captions are currently Arial in config; can switch to Montserrat if you install the font.
- **Thumbnail style (long-form):** one bold gold word + a striking image + minimal text. Consistency > cleverness.

## 5. "About" / channel description (paste into YouTube)

> Welcome to History Unearthed — your daily dose of the surprising, the forgotten, and the "wait, that's where that came from?" Every day we dig up one fascinating story from the past and tell it in under a minute (with longer deep-dives each week). Subscribe and never look at everyday life the same way again.
>
> New Shorts daily. New long-form videos weekly.
> 📩 Business: your-email@example.com

## 6. Channel settings checklist

In **YouTube Studio → Settings**:

- **Upload defaults:** set default description footer, default tags (the pipeline also sets per-video tags), category = **Education**, language = English.
- **Visibility:** keep first uploads **Private/Unlisted** (the pipeline defaults to `private`) until you've reviewed several.
- **Made for Kids:** set "No, not made for kids" at the channel level (pipeline also sets this per video).
- **Monetization:** you need 1,000 subscribers + 4,000 watch hours (or 10M Shorts views in 90 days) for YouTube Partner Program — keep that as the goal.
- **Branding watermark:** add the logo as a video watermark (Customisation → Branding).
- **Handle + URL:** set your `@handle` under Customisation → Basic info.

## 7. Posting cadence (matches your pipeline)

- **Daily:** 1 Short (`scripts/run_daily.sh` → `--format short`).
- **Weekly:** 1 long-form (e.g. Sundays → `--format long`). I can add weekday logic so the scheduler does this automatically.
- Best Shorts posting times skew evening/early-night in your audience's timezone — schedule the daily run a couple hours before.

## 8. Compliance reminders

- Use only royalty-free music (`assets/music/`). Pexels footage is free to use.
- Keep facts accurate and advertiser-friendly (the script prompt enforces this — still spot-check before going public).
- Vary topics (the pipeline de-dupes via `data/used_topics.json`) — genuinely informative, non-repetitive content performs best and stays policy-safe.
