# ReelPixly — Instagram-only downloader (beta)

A separate mobile-first prototype for Instagram content that users own or are authorised to save. Built for GitHub + Render Free **testing first**, before any future custom domain or Hostinger migration.

## Features

- Distinct SEO landing pages, titles, descriptions, canonical links and sitemap for Reels, videos, photos, carousels, Stories, Highlights, profile pictures and saved Live replays.
- Instagram-inspired gradient, Poppins with system fallback, responsive layout, dark/light mode.
- One URL field, **no preview step**, direct browser download as soon as an accessible file is ready.
- `yt-dlp` for authorised, accessible video posts and Reels; `Instaloader` for publicly accessible photo/carousel/profile media, when Instagram permits anonymous access.
- Errors are shown honestly: Stories and Highlights are **attempted for accessible videos only**, but generally require Instagram login and often fail on a public server (and photo Stories are not supported). Active Live streams, private content and DRM bypass are not supported. A saved Live recording may work if it is published as a normal public video post.
- Security basics: Instagram-only URL patterns, CDN allowlisting, bounded transfers, request limits, jobs cleaned after 10 minutes, no user cookies/passwords collected.

## Deploy to Render Free

1. Create a **new** GitHub repository named `reelpixly`. Do **not** modify your old `clipnest` repo.
2. Upload the **contents** of this project folder to the repository root (`Dockerfile` must be at the root).
3. In Render Dashboard choose **New → Web Service → Connect GitHub repo `reelpixly`**.
4. Language: **Docker**; Branch: `main`; Root directory: blank; Instance: **Free ($0/month)**.
5. Deploy and open your assigned `https://...onrender.com` address.
6. Verify `/api/health` returns JSON and all SEO pages load. Try Instagram links **only to content you can legitimately download**.

The included `render.yaml` is optional if using the manual dashboard setup above.

## Important operational limitations

This beta is not an unlimited or guaranteed downloader. Free Render services sleep while idle, have an ephemeral filesystem and limited memory/bandwidth; high-volume downloading is unsuitable. Instagram also blocks or changes its public endpoints frequently. Local testing uses mock downloads and does **not** establish live Instagram compatibility. For Render limits see: https://render.com/docs/free .

Settings are in `app.py`: 120 MB max per job, 15 media items, maximum 2 concurrent jobs, 18 requests per hour per client IP, 10-minute file expiry. In-memory jobs do not survive a restart.

## Future Google SEO / Analytics

Each page has a matching unique title, description and H1, plus canonical and `/sitemap.xml`. Search placement is not guaranteed. A custom domain is not needed during testing. Once one is available, set `PUBLIC_SITE_URL=https://your-domain.tld` in the Render environment and submit the sitemap to Search Console. Analytics and AdSense are **not activated** in this testing package; configure tracking with appropriate consent notices and verify compliance before enabling ads.

## Changes / structure

| File | Purpose |
|---|---|
| `app.py` | Backend, download jobs and page titles/SEO |
| `templates/index.html` | Page markup and sections |
| `static/styles.css` | Theme, colours, desktop/mobile design |
| `static/app.js` | Paste, theme toggle, download state handling |
| `static/favicon.svg` | Brand icon |
| `Dockerfile`, `requirements.txt` | Render deployment |
| `tests/test_site.py` | Mocked code and page smoke tests |
| `START_HERE_HINDI.txt` | Hindi walkthrough |

## Local run (optional)

```sh
pip install -r requirements.txt
uvicorn app:app --reload
```

Visit `http://127.0.0.1:8000/` and check `/api/health`.

Run tests with `pip install pytest httpx` then `pytest -q`. These tests mock the Instagram service, so they test code logic rather than platform availability.

ReelPixly is not affiliated with Instagram or Meta.
