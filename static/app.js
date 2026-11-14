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
      if (text) { input.value = text.trim(); input.dispatchEvent(new Event('input', {bubbles:true})); input.focus(); }
    } catch (_) { input.focus(); showStatus('Your browser blocked clipboard access. Press Ctrl+V to paste.', 'info'); }
  });

  const previewCard = document.querySelector('#media-preview');
  const previewImage = document.querySelector('#preview-image');
  const previewPlaceholder = document.querySelector('#preview-placeholder');
  const previewTitle = document.querySelector('#preview-title');
  const previewType = document.querySelector('#preview-type');
  const previewNote = document.querySelector('#preview-note');
  const confirmDownload = document.querySelector('#confirm-download');
  let currentPreview = null;
  let previewSequence = 0;
  let downloading = false;
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

  function showStatus(message, type='info', percent=null) {
    if (!status) return;
    status.hidden = false;
    status.className = `download-status ${type}`;
    status.textContent = '';
    const line = document.createElement('div');
    line.className = 'status-line';
    const dot = document.createElement('span'); dot.className = 'status-dot';
    const text = document.createElement('span'); text.textContent = message;
    line.append(dot, text); status.appendChild(line);
    if (typeof percent === 'number') {
      const track = document.createElement('div'); track.className = 'progress-track';
      const bar = document.createElement('span'); bar.className = 'progress-fill';
      bar.style.width = `${Math.max(2, Math.min(100, percent))}%`;
      track.appendChild(bar); status.appendChild(track);
    }
  }
  function resetPreview() {
    currentPreview = null;
    previewSequence++;
    if (previewCard) previewCard.hidden = true;
    if (previewImage) { previewImage.removeAttribute('src'); previewImage.hidden = true; }
    if (previewPlaceholder) previewPlaceholder.hidden = false;
    if (confirmDownload) confirmDownload.disabled = false;
  }
  input?.addEventListener('input', () => {
    resetPreview();
    if (status) status.hidden = true;
  });
  const setBusy = busy => {
    if (!downloadButton) return;
    downloadButton.disabled = busy;
    buttonLabel.textContent = busy ? 'Checking link...' : 'Show Preview';
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
  function getDetails() {
    const url = input.value.trim();
    if (!url) throw new Error('Please paste an Instagram link first.');
    let parsed;
    try { parsed = new URL(url); }
    catch (_) { throw new Error('Please paste a valid Instagram HTTPS link.'); }
    if (parsed.protocol !== 'https:' || !['instagram.com','www.instagram.com','m.instagram.com'].includes(parsed.hostname.toLowerCase()))
      throw new Error('Please paste a valid HTTPS instagram.com link.');
    return {url, mode: form.elements.namedItem('mode').value};
  }
  form?.addEventListener('submit', async ev => {
    ev.preventDefault();
    if (downloading) return;
    let details;
    try { details = getDetails(); }
    catch (e) { showStatus(e.message, 'error'); input.focus(); return; }
    resetPreview();
    const seq = previewSequence;
    setBusy(true);
    showStatus('Checking Instagram preview details...', 'loading');
    try {
      const preview = await jsonResponse('/api/preview', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(details)
      });
      // A changed URL or new submission invalidates the earlier preview.
      if (seq !== previewSequence) return;
      currentPreview = details;
      previewTitle.textContent = preview.title || 'Instagram media';
      previewType.textContent = (preview.kind || 'Instagram post') + (preview.count > 1 ? ` · ${preview.count} items` : '');
      previewNote.textContent = preview.message || 'Select Download to save permitted media.';
      previewImage.hidden = true;
      previewImage.removeAttribute('src');
      previewPlaceholder.hidden = false;
      if (preview.thumbnail) {
        previewImage.onload = () => { previewImage.hidden = false; previewPlaceholder.hidden = true; };
        previewImage.onerror = () => { previewImage.hidden = true; previewPlaceholder.hidden = false; };
        previewImage.src = preview.thumbnail;
      }
      previewCard.hidden = false;
      status.hidden = true;
      previewCard.scrollIntoView({behavior:'smooth', block:'nearest'});
    } catch (error) {
      if (seq === previewSequence) showStatus(error.message || 'Preview failed. Please try again.', 'error');
    } finally { setBusy(false); }
  });
  confirmDownload?.addEventListener('click', async () => {
    if (downloading || !currentPreview) return;
    downloading = true;
    confirmDownload.disabled = true;
    downloadButton.disabled = true;
    const details = currentPreview;
    showStatus('Preparing your download. Please keep this tab open...', 'loading', 8);
    try {
      const result = await jsonResponse('/api/download', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(details)
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
    finally { downloading = false; confirmDownload.disabled = false; downloadButton.disabled = false; }
  });
})();
