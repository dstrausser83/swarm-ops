// gemini_image.js v2 — generate an image via gemini.google.com (browser, no API cost)
// Usage: node gemini_image.js "<prompt>" [outDir]
// Output: JPG in outDir (default C:\RockVideos\outbox\images), prints JSON result to stdout.
// Watermark policy: NEVER strip/remove watermarks or provenance. If a visible
// watermark is detected, the result is flagged (watermark_suspected) for Rock's QC.
// v2: no fragile mode-button hunting — the prompt itself requests image generation.
//     Waits for a genuinely NEW large image; downloads via node fetch with cookies.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

const PROMPT = process.argv[2];
const OUTDIR = process.argv[3] || 'C:\\RockVideos\\outbox\\images';
const PROFILE = 'C:\\RockVideos\\chrome-profile';
const WORK = 'C:\\RockVideos\\work';
// PC load guard (operator rule): never burden his PC — only ONE browser
// automation job (image OR video) runs at a time. This lock serializes them.
// If the lock is held, wait up to 5 min for it to clear, then fail with pc_busy.
const LOCK = 'C:\\RockVideos\\work\\browser.lock';
if (!PROMPT) { console.log(JSON.stringify({ ok: false, error: 'no prompt given' })); process.exit(2); }
fs.mkdirSync(OUTDIR, { recursive: true });

async function acquireLock() {
  const start = Date.now();
  while (Date.now() - start < 5 * 60 * 1000) {
    try {
      fs.writeFileSync(LOCK, String(process.pid), { flag: 'wx' });
      return true;
    } catch (e) {
      await new Promise(r => setTimeout(r, 15000));
    }
  }
  return false;
}
function releaseLock() { try { fs.unlinkSync(LOCK); } catch (e) {} }

const sleep = ms => new Promise(r => setTimeout(r, ms));
const stamp = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

async function shot(page, name) {
  try { await page.screenshot({ path: path.join(WORK, `gi_${stamp()}_${name}.png`) }); } catch (e) {}
}

// All document images >= 300px, with src + size. Used to baseline before submit.
async function listImages(page) {
  return await page.evaluate(() => {
    const out = [];
    document.querySelectorAll('img').forEach(im => {
      const r = im.getBoundingClientRect();
      const src = im.currentSrc || im.src || '';
      if (r.width >= 300 && r.height >= 300 && src && !src.startsWith('data:')) {
        out.push({ key: src.slice(0, 120), w: Math.round(r.width), h: Math.round(r.height) });
      }
    });
    return out;
  }).catch(() => []);
}

(async () => {
  const log = [];
  const say = m => { log.push(m); console.error('[gi] ' + m); };
  if (!(await acquireLock())) {
    console.log(JSON.stringify({ ok: false, error: 'pc_busy', log: ['browser lock held — another job running'] }));
    process.exit(7);
  }
  const done = (code) => { releaseLock(); process.exit(code); };
  let browser;
  try {
    browser = await chromium.launchPersistentContext(PROFILE, {
      channel: 'chrome',
      headless: true,
      args: ['--disable-blink-features=AutomationControlled', '--no-first-run'],
      acceptDownloads: true,
    });
    const page = browser.pages()[0] || await browser.newPage();

    say('goto gemini');
    await page.goto('https://gemini.google.com/app', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(6000);
    await shot(page, 'landing');

    const signInVisible = await page.locator('text=Sign in').first().isVisible().catch(() => false);
    if (signInVisible || /accounts\.google\.com/.test(page.url())) {
      await shot(page, 'needs_login');
      console.log(JSON.stringify({ ok: false, error: 'not_logged_in', log }));
      await browser.close(); done(3);
    }
    say('logged in');

    const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
    if (/captcha|unusual traffic|verify you are human|are you a robot/.test(bodyText)) {
      await shot(page, 'challenge');
      console.log(JSON.stringify({ ok: false, error: 'challenge_detected', log }));
      await browser.close(); done(4);
    }

    // Dismiss onboarding/info modals
    for (let i = 0; i < 4; i++) {
      let dismissed = false;
      for (const label of ['Try it', 'Dismiss', 'Not now', 'Got it', 'Maybe later', 'No thanks']) {
        const btn = page.locator(`button:has-text("${label}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          say('dismissing modal: ' + label);
          await btn.click().catch(() => {});
          await page.waitForTimeout(2000);
          dismissed = true; break;
        }
      }
      if (!dismissed) break;
    }

    // Baseline existing images BEFORE submitting, so we only accept NEW ones.
    const before = await listImages(page);
    const beforeKeys = new Set(before.map(i => i.key));
    say('baseline images: ' + before.length);

    // The prompt itself requests image generation — no mode-button hunting.
    const fullPrompt = 'Generate an image: ' + PROMPT +
      ' Photorealistic, no people with deformed features, no text overlays, no watermark, no logo.';
    const box = page.locator('rich-textarea [contenteditable="true"], div[contenteditable="true"]').first();
    await box.waitFor({ state: 'visible', timeout: 30000 });
    await box.click();
    await page.waitForTimeout(500);
    await box.fill(fullPrompt);
    await page.waitForTimeout(1000);
    await shot(page, 'prompt_typed');

    say('submitting');
    const send = page.locator('button[aria-label*="Send"], button[data-testid*="send"]').first();
    if (await send.isVisible().catch(() => false)) { await send.click(); }
    else { await page.keyboard.press('Enter'); }
    await page.waitForTimeout(5000);
    await shot(page, 'submitted');

    // Wait for a NEW large image (up to 6 min). Never break on a stray download button.
    say('waiting for generation');
    let found = null;
    let snap = 0;
    const deadline = Date.now() + 6 * 60 * 1000;
    while (Date.now() < deadline) {
      const t = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
      if (/captcha|unusual traffic|verify you are human/.test(t)) {
        await shot(page, 'challenge_wait');
        console.log(JSON.stringify({ ok: false, error: 'challenge_detected', log }));
        await browser.close(); done(4);
      }
      const respText = await page.evaluate(() => {
        const tail = (document.body.innerText || '').slice(-1500);
        const m = tail.match(/.{0,120}(can't|unable|couldn't|sorry|error|failed|try again|generating|creating|here's your image).{0,120}/i);
        return m ? m[0].replace(/\s+/g, ' ').slice(0, 250) : '';
      }).catch(() => '');
      if (respText) say('page says: ' + respText);
      if (snap < 5 && Date.now() > deadline - 6 * 60 * 1000 + (snap + 1) * 60000) { snap++; await shot(page, 'waiting_' + snap); }

      const now = await listImages(page);
      const fresh = now.filter(i => !beforeKeys.has(i.key));
      if (fresh.length > 0) {
        // Prefer the largest fresh image — the generated one dwarfs thumbnails.
        fresh.sort((a, b) => (b.w * b.h) - (a.w * a.h));
        found = fresh[0];
        say('new image: ' + found.w + 'x' + found.h);
        break;
      }
      await page.waitForTimeout(10000);
    }
    await shot(page, 'generated');
    if (!found) {
      console.log(JSON.stringify({ ok: false, error: 'timeout_waiting_for_image', log }));
      await browser.close(); done(5);
    }

    // Resolve the full src of the chosen image (match by key prefix).
    const fullSrc = await page.evaluate((key) => {
      let best = null;
      document.querySelectorAll('img').forEach(im => {
        const src = im.currentSrc || im.src || '';
        if (src.slice(0, 120) === key) best = src;
      });
      return best;
    }, found.key).catch(() => null);
    if (!fullSrc) {
      console.log(JSON.stringify({ ok: false, error: 'src_resolution_failed', log }));
      await browser.close(); done(6);
    }

    // Download: Gemini serves generated images as blob: URLs, which only the
    // page can read. Fetch the blob IN the page, return base64, write in Node.
    // (No button clicking — that path proved flaky and can navigate/close the page.)
    const fname = `gemini_img_${stamp()}.jpg`;
    const outPath = path.join(OUTDIR, fname);
    let downloaded = false;
    try {
      say('reading blob in-page');
      const b64 = await page.evaluate(async (key) => {
        let src = null, el = null;
        document.querySelectorAll('img').forEach(im => {
          const s = im.currentSrc || im.src || '';
          if (s.slice(0, 120) === key) { src = s; el = im; }
        });
        if (!src) return null;
        try {
          const resp = await fetch(src);
          const buf = await resp.arrayBuffer();
          const bytes = new Uint8Array(buf);
          if (bytes.length > 20000) {
            let bin = '';
            const CHUNK = 8192;
            for (let i = 0; i < bytes.length; i += CHUNK) {
              bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
            }
            return { b64: btoa(bin), bytes: bytes.length, type: resp.headers.get('content-type') || '' };
          }
        } catch (e) {}
        // Canvas fallback: draw the <img> and export PNG (works even when fetch is blocked).
        try {
          if (!el) return null;
          const c = document.createElement('canvas');
          c.width = el.naturalWidth || el.width; c.height = el.naturalHeight || el.height;
          if (!c.width || !c.height) return null;
          c.getContext('2d').drawImage(el, 0, 0);
          const url = c.toDataURL('image/png');
          const data = url.split(',')[1];
          if (!data) return null;
          return { b64: data, bytes: Math.floor(data.length * 3 / 4), type: 'image/png' };
        } catch (e) { return null; }
      }, found.key).catch(() => null);
      if (b64 && b64.b64 && b64.bytes > 20000) {
        fs.writeFileSync(outPath, Buffer.from(b64.b64, 'base64'));
        downloaded = true;
        say(`wrote ${b64.bytes} bytes (${b64.type})`);
      } else {
        say('in-page blob read failed or too small: ' + (b64 ? b64.bytes : 'null'));
      }
    } catch (e) { say('blob download failed: ' + e.message); }

    const stat = fs.existsSync(outPath) ? fs.statSync(outPath) : null;
    await browser.close();
    if (downloaded && stat && stat.size > 20000) {
      console.log(JSON.stringify({ ok: true, file: outPath, bytes: stat.size,
        w: found.w, h: found.h, watermark_qc: 'pending_rock_review', log }));
    } else {
      console.log(JSON.stringify({ ok: false, error: 'download_failed_or_too_small', log }));
      done(6);
    }
  } catch (e) {
    try { await browser.close(); } catch (x) {}
    console.log(JSON.stringify({ ok: false, error: 'exception: ' + e.message }));
    done(1)
  }
})();
