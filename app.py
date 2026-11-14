"""ReelPixly: a small Instagram-only downloader for content users may save.

No login credential collection, session-cookie handling, private-content bypass,
DRM removal, or arbitrary URL proxying. This is a limited demo for Render Free.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import instaloader
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parent
TEMP = Path(os.environ.get('REELPIXLY_TEMP_DIR', Path(tempfile.gettempdir()) / 'reelpixly-media'))
TEMP.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 120 * 1024 * 1024  # Limit for each complete download job
MAX_ITEMS = 15
MAX_DURATION = 20 * 60
JOB_TTL = 10 * 60
MAX_ACTIVE = 2
logger = logging.getLogger('reelpixly')
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=MAX_ACTIVE)
jobs: dict[str, dict] = {}
rate_events: dict[str, deque] = defaultdict(deque)
# Instagram's 429 response is a provider-side restriction, not a problem to retry rapidly.
# These controls protect the public demo from creating unnecessary repeat requests.
INSTAGRAM_COOLDOWN_SECONDS = 15 * 60
PREVIEW_CACHE_SECONDS = 5 * 60
instagram_cooldown_until = 0.0  # monotonic clock
preview_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def instagram_rate_limited(exc: Exception) -> bool:
    message = str(exc).lower()
    return '429' in message or 'too many requests' in message or 'rate limit' in message


def mark_instagram_rate_limited(exc: Exception) -> bool:
    global instagram_cooldown_until
    if not instagram_rate_limited(exc):
        return False
    with lock:
        instagram_cooldown_until = max(
            instagram_cooldown_until,
            time.monotonic() + INSTAGRAM_COOLDOWN_SECONDS,
        )
        preview_cache.clear()
    logger.warning('Instagram returned a rate limit; pausing new extraction attempts on this service.')
    return True


def check_instagram_cooldown() -> None:
    with lock:
        remaining = instagram_cooldown_until - time.monotonic()
    if remaining > 0:
        raise HTTPException(
            status_code=503,
            detail='Instagram is temporarily rate-limiting this server. '
                   'Downloads and previews are paused to avoid repeated requests. Try again later.',
        )


def cached_preview(url: str, mode: str) -> dict | None:
    now = time.monotonic()
    with lock:
        cached = preview_cache.get((url, mode))
        if cached and cached[0] > now:
            return dict(cached[1])
        if cached:
            preview_cache.pop((url, mode), None)
    return None


def store_preview(url: str, mode: str, item: dict) -> None:
    if not item.get('details_available'):
        return
    with lock:
        if len(preview_cache) >= 100:
            preview_cache.clear()  # simple bounded best-effort cache for one free instance
        preview_cache[(url, mode)] = (time.monotonic() + PREVIEW_CACHE_SECONDS, dict(item))


PAGES = {
    'home': {
        'path': '/', 'label': 'All Downloads', 'icon': '✦',
        'title': 'Instagram Downloader – Reels, Videos, Photos & More | ReelPixly',
        'heading': 'Everything Instagram. One easy download.',
        'description': 'Save Instagram Reels, videos and accessible public photos you own or have permission to download. Preview first, then download when you are ready.',
        'lead': 'One simple place for Instagram Reels, videos, photos and more. Paste a permitted link and save it in a few clicks.',
        'type': 'auto', 'placeholder': 'Paste an Instagram Reel, post or profile link...',
        'faq': [('Is ReelPixly free to test?', 'Yes. This testing version runs on a free server with limits on size and downloads.'),
                ('Do I need to log in?', 'No Instagram login is requested. Content that requires a login is not available through this public demo.'),
                ('Can I download any Instagram content?', 'No. Only content that is accessible to the server and that you own or have permission to save.')]
    },
    'reels': {
        'path': '/instagram-reels-downloader', 'label': 'Reels', 'icon': '▶',
        'title': 'Instagram Reels Downloader – Save Reels Online | ReelPixly',
        'heading': 'Instagram Reels Downloader',
        'description': 'Download accessible Instagram Reels you own or have permission to save. Paste a Reel link into ReelPixly and preview the Reel first, then download when ready.',
        'lead': 'Save your favourite authorised Reel videos in a simple, clean download flow.',
        'type': 'reels', 'placeholder': 'https://www.instagram.com/reel/ABC123.../',
        'faq': [('How do I download an Instagram Reel?', 'Copy an accessible Reel URL, paste it above, then select Download.'),
                ('Can I download a private Reel?', 'No. Private or login-only content cannot be accessed by this public downloader.'),
                ('Why might a Reel fail?', 'Instagram may limit server access, or the Reel may be unavailable, private, or removed.')]
    },
    'video': {
        'path': '/instagram-video-downloader', 'label': 'Videos', 'icon': '▣',
        'title': 'Instagram Video Downloader – Download Videos Online | ReelPixly',
        'heading': 'Instagram Video Downloader',
        'description': 'Instagram Video Downloader for accessible public videos you own or may save. Paste a supported video post link and download.',
        'lead': 'A straightforward way to download permitted Instagram video posts in your browser.',
        'type': 'video', 'placeholder': 'https://www.instagram.com/p/ABC123.../',
        'faq': [('Which video links can I paste?', 'Try a direct Instagram video post, TV post or Reel URL.'),
                ('Can I choose a resolution?', 'This testing version chooses the best available compatible format automatically.'),
                ('Does it work with private posts?', 'No. It only tries content that is accessible without authentication.')]
    },
    'photo': {
        'path': '/instagram-photo-downloader', 'label': 'Photos', 'icon': '▧',
        'title': 'Instagram Photo Downloader – Download Images | ReelPixly',
        'heading': 'Instagram Photo Downloader',
        'description': 'Download accessible Instagram post photos you own or are authorised to save. Paste an Instagram photo post URL.',
        'lead': 'Save accessible Instagram photo posts with a quick preview before saving.',
        'type': 'photo', 'placeholder': 'https://www.instagram.com/p/ABC123.../',
        'faq': [('How do I save an Instagram photo?', 'Copy the photo post link, paste it into the field and press Download.'),
                ('Will every photo work?', 'No. Instagram may require login or restrict access; this site does not bypass those protections.'),
                ('What format are photos saved in?', 'Usually JPG, depending on the format provided by Instagram.')]
    },
    'carousel': {
        'path': '/instagram-carousel-downloader', 'label': 'Carousels', 'icon': '▦',
        'title': 'Instagram Carousel Downloader – Multiple Photos & Videos | ReelPixly',
        'heading': 'Instagram Carousel Downloader',
        'description': 'Save accessible Instagram carousel photos and videos you are authorised to download. Download multiple supported items as one ZIP.',
        'lead': 'Download accessible multi-photo and mixed-media posts together as one convenient ZIP.',
        'type': 'carousel', 'placeholder': 'Paste an Instagram carousel post link...',
        'faq': [('How are carousel items saved?', 'If the items are accessible, ReelPixly packages them into one ZIP file.'),
                ('How many items are supported?', 'The free testing build supports up to 15 items within its total file-size limit.'),
                ('Why might some carousel items fail?', 'Instagram may not expose all media to unauthenticated servers.')]
    },
    'story': {
        'path': '/instagram-story-downloader', 'label': 'Stories', 'icon': '◌',
        'title': 'Instagram Story Downloader – Save Stories | ReelPixly',
        'heading': 'Instagram Story Downloader',
        'description': 'Try to save accessible Instagram Stories you are authorised to download. Login-protected Stories are unavailable in this public demo.',
        'lead': 'Save accessible Stories you are allowed to download. Instagram often requires login, so success is not guaranteed.',
        'type': 'story', 'placeholder': 'Paste an Instagram Story link...',
        'faq': [('Can I save Stories from private accounts?', 'No. This public demo does not access private or login-protected Stories.'),
                ('Why does Story downloading say unavailable?', 'Instagram often requires a logged-in session, which ReelPixly does not request or store.'),
                ('How can I save my own Story?', 'Use the save or download controls in your Instagram account when available.')]
    },
    'highlight': {
        'path': '/instagram-highlights-downloader', 'label': 'Highlights', 'icon': '✺',
        'title': 'Instagram Highlights Downloader – Save Highlights | ReelPixly',
        'heading': 'Instagram Highlights Downloader',
        'description': 'Try accessible Instagram Highlight videos you may save. Login-only Highlights are not available in this public demo.',
        'lead': 'A separate page to try accessible Highlight videos. Instagram often requires login for Highlights, so success is not guaranteed.',
        'type': 'highlight', 'placeholder': 'Paste an Instagram Highlight link...',
        'faq': [('Can ReelPixly download all Highlights?', 'No. Many Instagram Highlights require authenticated access and are not supported by this demo.'),
                ('Can I download private Highlights?', 'No. Private content is not accessible here.'),
                ('Can I save my own Highlight?', 'Your Instagram account may provide options to save the original Story media.')]
    },
    'dp': {
        'path': '/instagram-profile-picture-downloader', 'label': 'Profile Photo', 'icon': '◉',
        'title': 'Instagram Profile Picture Downloader – Save DP | ReelPixly',
        'heading': 'Instagram Profile Picture Downloader',
        'description': 'Save accessible public Instagram profile photos you have permission to download. Paste a public profile URL.',
        'lead': 'A simple way to save accessible public profile pictures when Instagram allows server access.',
        'type': 'dp', 'placeholder': 'https://www.instagram.com/username/',
        'faq': [('Which link should I paste?', 'Paste an Instagram profile URL, not a photo post or Reel link.'),
                ('Can I download private profile pictures?', 'This service does not bypass private-account access restrictions.'),
                ('Why might a profile photo fail?', 'Instagram may restrict automated profile lookups from cloud hosting.')]
    },
    'live': {
        'path': '/instagram-live-video-downloader', 'label': 'Saved Lives', 'icon': '●',
        'title': 'Instagram Live Video Downloader – Saved Live Replays | ReelPixly',
        'heading': 'Instagram Saved Live Video Downloader',
        'description': 'Download accessible saved Instagram Live replay posts you are authorised to save. Active private Live streams are not supported.',
        'lead': 'For saved Live recordings published as accessible video posts. Ongoing Live streams cannot be downloaded here.',
        'type': 'live', 'placeholder': 'Paste a saved Live replay post URL...',
        'faq': [('Can I download a live broadcast while it is running?', 'No. This demo only accepts saved recordings published as accessible video posts.'),
                ('Which Live links may work?', 'Try a saved Live replay URL that opens as a normal Reel or video post.'),
                ('Do private live videos work?', 'No. Login-only streams and private recordings are not accessible here.')]
    },
}
NAV_KEYS = ('home','reels','video','photo','carousel','story','highlight','dp','live')
PATH_INDEX = {item['path']: key for key, item in PAGES.items()}

app = FastAPI(title='ReelPixly', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
templates = Jinja2Templates(directory=str(ROOT / 'templates'))

class DownloadRequest(BaseModel):
    url: str = Field(min_length=18, max_length=2048)
    mode: str = 'auto'


def validate_url(url: str) -> tuple[str, str, str]:
    """Validate restricted Instagram paths. Reject hosts/paths before network activity."""
    url = url.strip()
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        port = parsed.port
    except ValueError:
        raise HTTPException(400, 'Please paste a valid Instagram HTTPS link.') from None
    if (parsed.scheme != 'https' or host not in {'instagram.com','www.instagram.com','m.instagram.com'}
            or port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
        raise HTTPException(400, 'Only public Instagram HTTPS links are accepted.')
    path = parsed.path.rstrip('/') + '/'
    if re.fullmatch(r'/(p|reel|reels|tv)/[a-zA-Z0-9_-]{5,30}/', path):
        parts = path.strip('/').split('/')
        return f'https://www.instagram.com/{parts[0]}/{parts[1]}/', 'post', parts[1]
    if re.fullmatch(r'/stories/highlights/[0-9]{5,30}/', path):
        return f'https://www.instagram.com{path}', 'highlight', ''
    if re.fullmatch(r'/stories/[a-zA-Z0-9_.]{1,30}/[0-9]{5,30}/', path):
        return f'https://www.instagram.com{path}', 'story', ''
    if re.fullmatch(r'/[a-zA-Z0-9_.]{1,30}/', path):
        name = path.strip('/')
        if name.lower() not in {'accounts','explore','direct','stories','reel','reels','p','tv','live','about'}:
            return f'https://www.instagram.com/{name}/', 'profile', name
    raise HTTPException(400, 'Paste a direct Instagram Reel, post, Story, Highlight or profile link.')


def rate_limit(request: Request):
    key = request.client.host if request.client else 'unknown'
    now = time.time()
    with lock:
        hits = rate_events[key]
        while hits and hits[0] < now - 3600:
            hits.popleft()
        if len(hits) >= 18:
            raise HTTPException(429, 'Free test limit reached. Try again later.')
        hits.append(now)


def public_origin(request: Request) -> str:
    explicit = os.environ.get('PUBLIC_SITE_URL') or os.environ.get('RENDER_EXTERNAL_URL')
    if explicit and explicit.startswith('https://'):
        return explicit.rstrip('/')
    return str(request.base_url).rstrip('/')


def render_page(request: Request, key: str):
    page = PAGES[key]
    origin = public_origin(request)
    return templates.TemplateResponse(request, 'index.html', {
        'page': page, 'page_key': key, 'pages': PAGES, 'nav_keys': NAV_KEYS,
        'canonical': origin + page['path'], 'origin': origin,
        'faq_schema': {'@context': 'https://schema.org', '@type': 'FAQPage',
                       'mainEntity': [{'@type': 'Question', 'name': q,
                                       'acceptedAnswer': {'@type': 'Answer', 'text': a}}
                                      for q, a in page['faq']]},
    })


@app.get('/')
def home(request: Request):
    return render_page(request, 'home')


@app.get('/privacy')
def privacy(request: Request):
    return templates.TemplateResponse(request, 'legal.html', {'heading':'Privacy Policy','kind':'privacy'})


@app.get('/terms')
def terms(request: Request):
    return templates.TemplateResponse(request, 'legal.html', {'heading':'Terms of Use','kind':'terms'})


@app.get('/robots.txt')
def robots(request: Request):
    return PlainTextResponse(f'User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {public_origin(request)}/sitemap.xml\n')


@app.get('/sitemap.xml')
def sitemap(request: Request):
    from xml.sax.saxutils import escape
    origin = public_origin(request)
    urls = ''.join(f'<url><loc>{escape(origin + p["path"])}</loc></url>' for p in PAGES.values())
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', media_type='application/xml')


@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'ReelPixly', 'version': '1.0'}


def clean_error(exc: Exception) -> str:
    message = str(exc).lower()
    if any(w in message for w in ('login', 'log in', 'sign in', 'private', 'authentication', 'checkpoint', 'cookies')):
        return 'Instagram requires login or restricted access for this link. This public downloader cannot access it.'
    if any(w in message for w in ('429', 'rate limit', 'too many requests', 'blocked', '403', '401')):
        return 'Instagram is limiting access from this server. Please try again later with permitted public content.'
    if any(w in message for w in ('not found', '404', 'removed', 'unavailable')):
        return 'This Instagram content is unavailable or the link has expired.'
    if any(w in message for w in ('size limit', 'file too large', 'too large', 'maximum file size')):
        return 'This item is larger than the free 120 MB download limit.'
    if 'duration' in message or 'too long' in message:
        return 'This test build supports video recordings up to 20 minutes.'
    return 'Could not download this link. Instagram may restrict access or this content type may not be available.'


def update(job_id: str, **values):
    with lock:
        if job_id in jobs:
            jobs[job_id].update(values)
            jobs[job_id]['updated'] = time.time()


def cleanup():
    now = time.time()
    expired = []
    with lock:
        for jid, item in list(jobs.items()):
            if item['status'] in {'ready','error'} and now - item['updated'] > JOB_TTL:
                expired.append(Path(item['folder']))
                del jobs[jid]
    for folder in expired:
        shutil.rmtree(folder, ignore_errors=True)


def file_candidates(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() not in {'.part','.ytdl','.json','.tmp'}]


def guard_files(folder: Path):
    files = file_candidates(folder)
    if not files:
        raise ValueError('No downloadable media found')
    if len(files) > MAX_ITEMS:
        raise ValueError('Too many items for the free demo')
    if sum(f.stat().st_size for f in files) > MAX_BYTES:
        raise ValueError('File too large: size limit')
    return files


def yt_video_download(url: str, folder: Path, job_id: str, *, playlist: bool = False):
    """Fallback for accessible videos and Reels; no cookies or private content."""
    def hook(data):
        if data.get('status') == 'downloading':
            done = data.get('downloaded_bytes') or 0
            if done > MAX_BYTES:
                raise ValueError('File too large: size limit')
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            update(job_id, progress=round(100 * done / total) if total else None)
    def match_filter(info, *, incomplete):
        if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming'}:
            return 'Live broadcast cannot be downloaded'
        duration = info.get('duration')
        if duration and duration > MAX_DURATION:
            return 'Duration too long'
        return None
    opts = {
        'quiet': True, 'no_warnings': True, 'noplaylist': not playlist,
        'cachedir': False, 'retries': 1, 'fragment_retries': 1,
        'socket_timeout': 20, 'max_filesize': MAX_BYTES,
        'outtmpl': str(folder / 'reelpixly_%(id)s.%(ext)s'),
        'format': 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best',
        'merge_output_format': 'mp4', 'progress_hooks': [hook],
        'match_filter': match_filter,
        'playlistend': MAX_ITEMS if playlist else 1,
    }
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    guard_files(folder)


def validate_cdn_url(url: str):
    """Validate URLs obtained from Instaloader only; reject internal network targets."""
    try:
        p = urlparse(url)
        host = (p.hostname or '').lower().rstrip('.')
        port = p.port
    except ValueError:
        raise ValueError('Unsafe media URL') from None
    allowed = ('fbcdn.net','cdninstagram.com','instagram.com')
    if p.scheme != 'https' or port not in (None, 443) or p.username or p.password or not any(host == d or host.endswith('.'+d) for d in allowed):
        raise ValueError('Unsafe media host')
    # Defence in depth against private IPs and DNS rebinding on redirect targets.
    try:
        for result in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
            addr = ipaddress.ip_address(result[4][0])
            if not addr.is_global:
                raise ValueError('Nonpublic media address')
    except OSError:
        raise ValueError('Media host could not be resolved') from None


def save_media(url: str, destination: Path, used: list[int]):
    current = url
    with requests.Session() as session:
        for _ in range(4):
            validate_cdn_url(current)
            with session.get(current, stream=True, timeout=(10, 30), allow_redirects=False,
                             headers={'User-Agent': 'Mozilla/5.0 (ReelPixly public-media downloader)'}) as r:
                if r.status_code in (301,302,303,307,308):
                    from urllib.parse import urljoin
                    current = urljoin(current, r.headers.get('Location',''))
                    continue
                r.raise_for_status()
                mime = r.headers.get('Content-Type','').lower()
                if not (mime.startswith('image/') or mime.startswith('video/') or 'octet-stream' in mime):
                    raise ValueError('Instagram did not return a media file')
                expected = int(r.headers.get('content-length') or 0)
                if expected and used[0] + expected > MAX_BYTES:
                    raise ValueError('File too large: size limit')
                with destination.open('wb') as out:
                    for block in r.iter_content(chunk_size=128*1024):
                        if block:
                            used[0] += len(block)
                            if used[0] > MAX_BYTES:
                                raise ValueError('File too large: size limit')
                            out.write(block)
                if destination.stat().st_size < 100:
                    raise ValueError('Instagram returned an empty media response')
                return
    raise ValueError('Too many media redirects')


def instagram_post_download(shortcode: str, folder: Path, mode: str, job_id: str):
    """Public post photos/carousels via Instaloader; may be blocked by Instagram."""
    loader = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=1, fatal_status_codes=[429], download_pictures=False,
                                    download_videos=False, download_video_thumbnails=False,
                                    save_metadata=False)
    post = instaloader.Post.from_shortcode(loader.context, shortcode)
    items = []
    if post.typename == 'GraphSidecar':
        for node in post.get_sidecar_nodes():
            items.append(('video' if node.is_video else 'photo', node.video_url if node.is_video else node.display_url))
    else:
        items.append(('video' if post.is_video else 'photo', post.video_url if post.is_video else post.url))
    if not items:
        raise ValueError('No media available')
    if mode == 'carousel' and len(items) == 1:
        raise ValueError('This post is not a carousel')
    if mode == 'photo' and all(t != 'photo' for t,_ in items):
        raise ValueError('This post contains video, not a photo')
    if mode in {'video','reels','live'} and all(t != 'video' for t,_ in items):
        raise ValueError('This post has photos only; try Photo Downloader')
    if mode == 'photo':
        items = [(t,u) for t,u in items if t == 'photo']
    elif mode in {'video','reels','live'}:
        items = [(t,u) for t,u in items if t == 'video']
    if len(items) > MAX_ITEMS:
        raise ValueError('This carousel has too many items for the free demo')
    used = [0]
    for i,(kind,url) in enumerate(items,1):
        ext = '.mp4' if kind == 'video' else '.jpg'
        save_media(url, folder / f'reelpixly_{shortcode}_{i:02d}{ext}', used)
        update(job_id, progress=round(i / len(items) * 100))
    guard_files(folder)


def profile_download(username: str, folder: Path):
    loader = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=1, fatal_status_codes=[429], download_pictures=False,
                                    download_videos=False, download_video_thumbnails=False,
                                    save_metadata=False)
    profile = instaloader.Profile.from_username(loader.context, username)
    used = [0]
    save_media(str(profile.profile_pic_url), folder / f'reelpixly_{username}_profile.jpg', used)
    guard_files(folder)


def run_job(job_id: str, url: str, kind: str, identifier: str, mode: str):
    with lock:
        folder = Path(jobs[job_id]['folder'])
    update(job_id, status='downloading', progress=None)
    try:
        if kind == 'profile':
            profile_download(identifier, folder)
        elif kind in {'story', 'highlight'}:
            # Anonymous, accessible Story/Highlight video URLs only.
            # Most currently require login; there is no cookie bypass.
            yt_video_download(url, folder, job_id, playlist=(kind == 'highlight'))
        elif mode in {'reels','video','live'}:
            try:
                yt_video_download(url, folder, job_id)
            except Exception as first:
                if instagram_rate_limited(first):
                    raise  # Do not generate another request after provider 429
                logger.info('yt-dlp unavailable for this video; trying public post metadata: %s', first)
                instagram_post_download(identifier, folder, mode, job_id)
        else:
            # Photo, carousel, and auto: Instaloader includes static images.
            try:
                instagram_post_download(identifier, folder, mode, job_id)
            except Exception as first:
                if instagram_rate_limited(first) or mode != 'auto':
                    raise
                logger.info('Post metadata unavailable; attempting video fallback: %s', first)
                yt_video_download(url, folder, job_id)
        update(job_id, status='packaging', progress=100)
        files = guard_files(folder)
        if len(files) > 1:
            archive = folder / 'ReelPixly_Instagram_Download.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as z:
                for file in files:
                    z.write(file, file.name)
            result = archive
        else:
            result = files[0]
        update(job_id, status='ready', path=str(result), filename=result.name,
               size=result.stat().st_size, progress=100)
    except Exception as exc:
        mark_instagram_rate_limited(exc)
        logger.warning('Instagram download failed [mode=%s]: %s',mode,str(exc)[:300])
        update(job_id, status='error', error=clean_error(exc))
        shutil.rmtree(folder, ignore_errors=True)


# Preview only checks public media metadata. It must NEVER create a download job.
# When Instagram blocks metadata, a clearly labelled basic URL card is shown,
# so the user can still decide whether to try downloading permitted content.
def safe_preview_image(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        allowed = ('fbcdn.net', 'cdninstagram.com', 'instagram.com')
        if parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username and not parsed.password and any(host == x or host.endswith('.' + x) for x in allowed):
            return url
    except ValueError:
        pass
    return None


def basic_preview(kind: str, mode: str, identifier: str) -> dict:
    labels = {'reels':'Reel', 'video':'Video', 'photo':'Photo', 'carousel':'Carousel',
              'story':'Story', 'highlight':'Highlight', 'dp':'Profile photo',
              'live':'Saved Live', 'auto':'Instagram post'}
    label = ('Profile photo' if kind == 'profile' else labels.get(mode, 'Instagram post'))
    return {'title': f'Instagram {label}', 'kind': label, 'thumbnail': None,
            'count': None, 'details_available': False,
            'message': 'Instagram did not provide a thumbnail for this link. You can still try downloading permitted content.'}


def validate_download_mode(kind: str, mode: str) -> None:
    if mode not in {'auto','reels','video','photo','carousel','story','highlight','dp','live'}:
        raise HTTPException(400, 'Please choose a valid download type.')
    if mode in {'story','highlight'} and kind != mode:
        raise HTTPException(422, 'Paste the matching Instagram Story or Highlight URL for this downloader.')
    if kind in {'story','highlight'} and mode not in {'auto',kind}:
        raise HTTPException(422, 'Choose the matching Story or Highlights downloader for this link.')
    if kind == 'profile' and mode not in {'dp','auto'}:
        raise HTTPException(422, 'Use the Profile Photo Downloader for Instagram profile links.')
    if kind == 'post' and mode == 'dp':
        raise HTTPException(422, 'Paste a public Instagram profile URL for profile pictures.')
    if mode == 'live' and kind != 'post':
        raise HTTPException(422, 'Only saved Live recordings published as video posts are supported.')


@app.post('/api/preview')
def preview(payload: DownloadRequest, request: Request):
    rate_limit(request)
    url, kind, identifier = validate_url(payload.url)
    mode = payload.mode
    validate_download_mode(kind, mode)
    check_instagram_cooldown()
    previously = cached_preview(url, mode)
    if previously:
        return previously
    item = basic_preview(kind, mode, identifier)
    try:
        if kind == 'profile':
            loader = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=1, fatal_status_codes=[429],
                                            download_pictures=False, download_videos=False,
                                            download_video_thumbnails=False, save_metadata=False)
            profile = instaloader.Profile.from_username(loader.context, identifier)
            item.update(title=f'@{profile.username}', kind='Profile photo',
                        thumbnail=safe_preview_image(str(profile.profile_pic_url)),
                        count=1, details_available=True,
                        message='Public profile picture preview')
        elif kind == 'post':
            # Instaloader includes previews for photo posts, videos, and carousels.
            try:
                loader = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=1, fatal_status_codes=[429],
                                                download_pictures=False, download_videos=False,
                                                download_video_thumbnails=False, save_metadata=False)
                post = instaloader.Post.from_shortcode(loader.context, identifier)
                count = int(post.mediacount) if post.typename == 'GraphSidecar' else 1
                title = (post.caption or '').strip().splitlines()[0][:105] if post.caption else ''
                post_kind = 'Carousel' if count > 1 else ('Video' if post.is_video else 'Photo')
                item.update(title=title or f'Instagram {post_kind}', kind=post_kind,
                            thumbnail=safe_preview_image(str(post.url)),
                            count=count, details_available=True,
                            message='Cover image shown. Click Download to save the available file.')
            except Exception as first:
                if instagram_rate_limited(first):
                    raise  # Stop immediately; do not try another extractor after 429
                # Alternate metadata lookup for public video/Reel links.
                with YoutubeDL({'quiet': True, 'no_warnings': True, 'noplaylist': True,
                                'cachedir': False, 'socket_timeout': 12, 'retries': 0,
                                'skip_download': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                if info:
                    title = str(info.get('title') or '').strip()[:105]
                    item.update(title=title or 'Instagram video', kind='Reel / Video',
                                thumbnail=safe_preview_image(info.get('thumbnail')),
                                count=1, details_available=True,
                                message='Cover image shown. Click Download to save the available file.')
        # Anonymous Story / Highlight metadata is commonly login-protected.
    except Exception as exc:
        if mark_instagram_rate_limited(exc):
            raise HTTPException(
                status_code=503,
                detail='Instagram is temporarily limiting this server. Preview unavailable; please try again later.',
            ) from None
        logger.info('Preview metadata unavailable: %s', str(exc)[:160])
        # Keep honest about real thumbnails: a generic card is not an actual preview.
        raise HTTPException(
            status_code=502,
            detail='A real Instagram preview could not be retrieved for this link. No download started.',
        ) from None
    if not item.get('details_available'):
        raise HTTPException(
            status_code=422,
            detail='A real preview is not available for this content without authorised Instagram access.',
        )
    store_preview(url, mode, item)
    return item


@app.post('/api/download', status_code=202)
def download(payload: DownloadRequest, request: Request):
    rate_limit(request)
    url, kind, identifier = validate_url(payload.url)
    mode = payload.mode
    validate_download_mode(kind, mode)
    check_instagram_cooldown()
    cleanup()
    with lock:
        active = sum(1 for j in jobs.values() if j['status'] not in {'ready','error'})
        if active >= MAX_ACTIVE:
            raise HTTPException(429, 'The free testing server is busy. Try again shortly.')
        jid = uuid.uuid4().hex
        folder = TEMP / jid
        folder.mkdir(parents=True, exist_ok=False)
        jobs[jid] = {'status':'queued','updated':time.time(),'folder':str(folder),
                     'progress':None,'error':None,'filename':None,'path':None,'size':None}
    executor.submit(run_job, jid, url, kind, identifier, mode)
    return {'job_id':jid}


@app.get('/api/jobs/{job_id}')
def job_status(job_id: str):
    cleanup()
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download not found or expired.')
        return {'status':job['status'],'progress':job['progress'],'error':job['error'],
                'filename':job['filename'],'size':job['size'],
                'download_url':f'/api/files/{job_id}' if job['status']=='ready' else None}


@app.get('/api/files/{job_id}')
def deliver(job_id: str):
    cleanup()
    with lock:
        job = jobs.get(job_id)
        if not job or job['status']!='ready' or not job['path']:
            raise HTTPException(404, 'File not available or expired.')
        path = Path(job['path'])
        name = job['filename']
    if not path.is_file():
        raise HTTPException(404, 'Temporary file expired. Please try again.')
    return FileResponse(path, filename=name, media_type='application/octet-stream', headers={'Cache-Control':'no-store'})


@app.get('/{slug}')
def landing(slug: str, request: Request):
    path = '/' + slug
    if path not in PATH_INDEX:
        raise HTTPException(404, 'Page not found')
    return render_page(request, PATH_INDEX[path])


