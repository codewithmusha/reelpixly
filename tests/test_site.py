"""Run: pip install -r requirements.txt pytest httpx ; pytest -q

The tests mock platform calls; they do not claim Instagram works from Render.
"""
import importlib.util
import sys
import html
import time
import types
from pathlib import Path
from unittest.mock import patch

# Enable offline local smoke-tests when third-party downloader modules are not installed.
if importlib.util.find_spec('yt_dlp') is None:
    fake = types.ModuleType('yt_dlp')
    class StubYoutubeDL: pass
    fake.YoutubeDL = StubYoutubeDL
    sys.modules['yt_dlp'] = fake
if importlib.util.find_spec('instaloader') is None:
    fake = types.ModuleType('instaloader')
    fake.Instaloader = object
    fake.Post = types.SimpleNamespace(from_shortcode=None)
    fake.Profile = types.SimpleNamespace(from_username=None)
    sys.modules['instaloader'] = fake

from fastapi.testclient import TestClient
import app as site

client = TestClient(site.app)

class FakeYoutubeDL:
    def __init__(self, opts): self.opts = opts
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def download(self, urls):
        folder = Path(self.opts['outtmpl']).parent
        folder.mkdir(exist_ok=True, parents=True)
        (folder / 'mock_video.mp4').write_bytes(b'mock mp4 downloaded bytes')
        self.opts['progress_hooks'][0]({'status':'downloading','downloaded_bytes':10,'total_bytes':100})
        return 0


def test_home_pages_render_seo():
    home = client.get('/')
    assert home.status_code == 200, home.text
    assert 'ReelPixly' in home.text and 'Instagram moments' in home.text
    assert 'Poppins' in home.text and 'id="download-form"' in home.text
    for key, p in site.PAGES.items():
        result = client.get(p['path'])
        assert result.status_code == 200, (key, result.text[:500])
        assert f'<title>{p["title"]}</title>' in html.unescape(result.text)
        assert p['description'] in html.unescape(result.text)
        assert 'rel="canonical"' in result.text
    assert client.get('/privacy').status_code == 200
    assert 'Privacy Policy' in client.get('/privacy').text
    assert client.get('/terms').status_code == 200
    assert client.get('/non-existent-link').status_code == 404


def test_sitemap_and_robots():
    xml = client.get('/sitemap.xml')
    assert xml.status_code == 200
    assert 'instagram-reels-downloader' in xml.text
    assert len(xml.text.split('<url>')) - 1 == len(site.PAGES)
    robots = client.get('/robots.txt').text
    assert 'Sitemap:' in robots
    assert 'Disallow: /api/' in robots
    assert client.get('/api/health').json()['ok'] is True


def test_input_validation():
    samples = [
        'https://www.instagram.com/reel/DfABC1234/',
        'https://www.instagram.com/p/DfABC1234/',
        'https://www.instagram.com/stories/name123/1234567890/',
        'https://www.instagram.com/stories/highlights/1234567890/',
        'https://www.instagram.com/safe.username/',
    ]
    for sample in samples:
        assert site.validate_url(sample)[0].startswith('https://www.instagram.com/')
    for bad in ['https://instagram.com.evil.com/reel/abcdef/',
                'http://instagram.com/reel/abcdef/',
                'https://127.0.0.1/reel/abcdef/',
                'https://instagram.com:8080/p/abcdef/',
                'https://instagram.com/api/v1/hello',
                'https://instagram.com@attacker.com/reel/abcdef/',
                'https://www.instagram.com/p/../../etc/passwd']:
        try: site.validate_url(bad)
        except Exception: pass
        else: assert False, bad


def test_direct_video_download(tmp_path):
    site.TEMP = tmp_path
    with patch.object(site,'YoutubeDL',FakeYoutubeDL):
        result = client.post('/api/download', json={'url':'https://www.instagram.com/reel/ABCDEFG123/', 'mode':'reels'})
        assert result.status_code == 202, result.text
        job = result.json()['job_id']
        for _ in range(100):
            record = client.get('/api/jobs/' + job).json()
            if record['status'] in ('ready','error'): break
            time.sleep(.015)
        assert record['status']=='ready', record
        assert record['download_url']
        file = client.get(record['download_url'])
        assert file.status_code==200
        assert file.content == b'mock mp4 downloaded bytes'


def test_unsupported_modes_are_honest():
    result = client.post('/api/download',json={'url':'https://www.instagram.com/stories/name123/1234567890/','mode':'photo'})
    assert result.status_code==422
    assert 'story' in result.json()['detail'].lower()
    result = client.post('/api/download',json={'url':'https://www.instagram.com/p/ABCDEFG123/','mode':'dp'})
    assert result.status_code==422
    result = client.post('/api/download',json={'url':'https://www.instagram.com/name123/','mode':'video'})
    assert result.status_code==422


def test_cdn_allowlist():
    for url in ['http://scontent.cdninstagram.com/img.jpg', 'https://localhost/file',
                'https://cdninstagram.com.evil.com/f', 'https://169.254.169.254/file']:
        try: site.validate_cdn_url(url)
        except ValueError: pass
        else: assert False, url
