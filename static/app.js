(() => {
  'use strict';
  const root = document.documentElement;
  const themeToggle = document.querySelector('#theme-toggle');
  const form = document.querySelector('#download-form');
  const input = document.querySelector('#media-url');
  const pasteButton = document.querySelector('#paste-button');
  const downloadButton = document.querySelector('#download-button');
  const buttonLabel = document.querySelector('#button-label');
  const status = document.querySelector('#download-status');

  themeToggle?.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('reelpixly-theme', next); } catch (_) {}
  });

  pasteButton?.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) { input.value = text.trim(); input.focus(); }
    } catch (_) { input.focus(); showStatus('Your browser blocked clipboard access. Press Ctrl+V to paste.', 'info'); }
  });

  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  function showStatus(message, type='info', percent=null) {
    if (!status) return;
    status.hidden = false;
    status.className = `download-status ${type}`;
    status.textContent = '';
    const line = document.createElement('div');
    line.className = 'status-line';
    const dot = document.createElement('span'); dot.className = 'status-dot';
    line.appendChild(dot);
    const text = document.createElement('span'); text.textContent = message;
    line.appendChild(text);
    status.appendChild(line);
    if (typeof percent === 'number') {
      const track = document.createElement('div'); track.className = 'progress-track';
      const bar = document.createElement('span'); bar.className = 'progress-fill';
      bar.style.width = `${Math.max(2, Math.min(100, percent))}%`;
      track.appendChild(bar); status.appendChild(track);
    }
  }
  const setBusy = busy => {
    if (!downloadButton) return;
    downloadButton.disabled = busy;
    buttonLabel.textContent = busy ? 'Preparing your file...' : 'Download Now';
  };
  async function jsonResponse(url, options) {
    const res = await fetch(url, options);
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'string' ? detail : 'Something went wrong. Please try again.');
    }
    return data;
  }
  form?.addEventListener('submit', async ev => {
    ev.preventDefault();
    let url = input.value.trim();
    if (!url) { showStatus('Please paste an Instagram link first.', 'error'); input.focus(); return; }
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'https:' || !['instagram.com','www.instagram.com','m.instagram.com'].includes(parsed.hostname.toLowerCase())) {
        showStatus('Please paste a valid HTTPS instagram.com link.', 'error'); input.focus(); return;
      }
    } catch (_) { showStatus('This does not look like a valid Instagram link.', 'error'); return; }
    const mode = form.elements.namedItem('mode').value;
    setBusy(true);
    showStatus('Connecting to Instagram. Please keep this tab open...', 'loading', 8);
    try {
      const result = await jsonResponse('/api/download', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({url, mode})
      });
      for (let attempt=0; attempt<90; attempt++) {
        await sleep(2000);
        const job = await jsonResponse(`/api/jobs/${encodeURIComponent(result.job_id)}`);
        if (job.status === 'error') throw new Error(job.error || 'Unable to access this media.');
        if (job.status === 'ready' && job.download_url) {
          showStatus('File ready! Your download should start now.', 'success', 100);
          const link = document.createElement('a');
          link.href = job.download_url; link.style.display = 'none';
          document.body.appendChild(link); link.click(); link.remove();
          // Some browsers block automatic downloads after an async request.
          // Keep a visible direct link as a no-preview fallback.
          const manual = document.createElement('a');
          manual.href = job.download_url;
          manual.className = 'manual-download';
          manual.textContent = 'If it did not start, tap here to save the file';
          status.appendChild(manual);
          return;
        }
        const stage = job.status === 'packaging' ? 'Packing your files...' : 'Downloading the available media...';
        showStatus(stage, 'loading', typeof job.progress === 'number' ? job.progress : null);
      }
      throw new Error('The server took too long. Please try a shorter public link.');
    } catch (error) { showStatus(error.message || 'Download failed. Please try again.', 'error'); }
    finally { setBusy(false); }
  });
})();
