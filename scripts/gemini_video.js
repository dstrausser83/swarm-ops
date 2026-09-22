// gemini_video.js — generate a video via gemini.google.com (browser, no API cost)
// Usage: node gemini_video.js "<prompt>" [outDir]
// Output: MP4 in outDir (default C:\RockVideos\outbox), prints JSON result to stdout.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const PROMPT = process.argv[2];
const OUTDIR = process.argv[3] || 'C:\\RockVideos\\outbox';
const PROFILE = 'C:\\RockVideos\\chrome-profile';
const WORK = 'C:\\RockVideos\\work';
// PC load guard (operator rule): never burden his PC — only ONE browser
// automation job (image OR video) runs at a time. Shared lock with gemini_image.js.
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
  try { await page.screenshot({ path: path.join(WORK, `vg_${stamp()}_${name}.png`) }); } catch (e) {}
}

(async () => {
  const log = [];
  const say = m => { log.push(m); console.error('[vg] ' + m); };
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

    // Login check: look for Sign in button
    const signInVisible = await page.locator('text=Sign in').first().isVisible().catch(() => false);
    const url = page.url();
    if (signInVisible || /accounts\.google\.com/.test(url)) {
      await shot(page, 'needs_login');
      console.log(JSON.stringify({ ok: false, error: 'not_logged_in', log }));
      await browser.close();
      done(3);
    }
    say('logged in');

    // CAPTCHA / challenge check
    const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
    if (/captcha|unusual traffic|verify you are human|are you a robot/.test(bodyText)) {
      await shot(page, 'challenge');
      console.log(JSON.stringify({ ok: false, error: 'challenge_detected', log }));
      await browser.close();
      done(4);
    }

    // Open the tools/menu to select Video (Veo). The prompt bar has a "+" or tools button.
    // Strategy: click the model/tools affordance, then pick Video.
    say('selecting video mode');
    let videoMode = false;
    // Try the "Video" chip directly if visible
    const videoChip = page.locator('button:has-text("Video"), [aria-label*="Video"]').first();
    if (await videoChip.isVisible().catch(() => false)) {
      await videoChip.click().catch(() => {});
      videoMode = true;
    } else {
      // Open the + / tools menu
      const plus = page.locator('button[aria-label*="Add"], button[aria-label*="Upload"], button:has-text("+")').first();
      if (await plus.isVisible().catch(() => false)) {
        await plus.click().catch(() => {});
        await page.waitForTimeout(1500);
        await shot(page, 'tools_menu');
        const v = page.locator('button:has-text("Video"), [role="menuitem"]:has-text("Video")').first();
        if (await v.isVisible().catch(() => false)) { await v.click().catch(() => {}); videoMode = true; }
      }
    }
    await page.waitForTimeout(2000);
    await shot(page, 'video_mode');
    say('video mode selected: ' + videoMode);

    // Dismiss any onboarding/info modals (loop: "Try it", "Dismiss", "Not now", "Got it", "Maybe later")
    for (let i = 0; i < 4; i++) {
      let dismissed = false;
      for (const label of ['Try it', 'Dismiss', 'Not now', 'Got it', 'Maybe later', 'No thanks']) {
        const btn = page.locator(`button:has-text("${label}")`).first();
        if (await btn.isVisible().catch(() => false)) {
          say('dismissing modal: ' + label);
          await btn.click().catch(() => {});
          await page.waitForTimeout(2000);
          dismissed = true;
          break;
        }
      }
      if (!dismissed) break;
    }
    await shot(page, 'after_modals');

    // Switch aspect ratio to portrait/vertical for reels if a selector is present
    try {
      const arBtn = page.locator('button:has-text("Landscape"), button:has-text("16:9"), [aria-label*="Aspect"]').first();
      if (await arBtn.isVisible().catch(() => false)) {
        await arBtn.click().catch(() => {});
        await page.waitForTimeout(1200);
        await shot(page, 'aspect_menu');
        const portrait = page.locator('button:has-text("Portrait"), [role="menuitem"]:has-text("Portrait"), button:has-text("9:16"), [role="menuitem"]:has-text("9:16")').first();
        if (await portrait.isVisible().catch(() => false)) {
          await portrait.click().catch(() => {});
          say('aspect set to portrait');
          await page.waitForTimeout(1500);
        } else {
          await page.keyboard.press('Escape');
          say('portrait option not found, keeping default');
        }
      }
    } catch (e) { say('aspect switch skipped: ' + e.message); }
    await shot(page, 'ready_to_type');

    // Type the prompt. Keep text in safe margins: instruct in prompt.
    const fullPrompt = PROMPT + ' Keep all on-screen text well inside the frame with wide safe margins on every edge; no text near the borders.';
    const box = page.locator('rich-textarea [contenteditable="true"], div[contenteditable="true"]').first();
    await box.waitFor({ state: 'visible', timeout: 30000 });
    await box.click();
    await page.waitForTimeout(500);
    await box.fill(fullPrompt);
    await page.waitForTimeout(1000);
    await shot(page, 'prompt_typed');

    // Submit
    say('submitting');
    const send = page.locator('button[aria-label*="Send"], button[data-testid*="send"]').first();
    if (await send.isVisible().catch(() => false)) { await send.click(); }
    else { await page.keyboard.press('Enter'); }
    await page.waitForTimeout(5000);
    await shot(page, 'submitted');

    // Wait for generation to finish: poll for a video element or download button (up to 12 min)
    say('waiting for generation');
    let videoEl = null;
    let snap = 0;
    const deadline = Date.now() + 12 * 60 * 1000;
    while (Date.now() < deadline) {
      // challenge check during wait
      const t = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
      if (/captcha|unusual traffic|verify you are human/.test(t)) {
        await shot(page, 'challenge_wait');
        console.log(JSON.stringify({ ok: false, error: 'challenge_detected', log }));
        await browser.close(); done(4);
      }
      // Log visible response/error text so we can see what Gemini is doing
      const respText = await page.evaluate(() => {
        const body = document.body.innerText || '';
        const tail = body.slice(-2000);
        const m = tail.match(/.{0,150}(can't|unable|couldn't|sorry|error|failed|try again|generating|creating your video).{0,150}/i);
        return m ? m[0].replace(/\s+/g, ' ').slice(0, 300) : '';
      }).catch(() => '');
      if (respText) say('page says: ' + respText);
      // periodic screenshot every ~60s
      if (Date.now() > deadline - 12 * 60 * 1000 + snap * 60000 + 45000) { snap++; await shot(page, 'waiting_' + snap); }
      videoEl = page.locator('video').first();
      if (await videoEl.count() > 0) {
        const src = await videoEl.getAttribute('src').catch(() => null) || await videoEl.evaluate(v => v.currentSrc || '').catch(() => '');
        if (src) { say('video element ready: ' + String(src).slice(0, 60)); break; }
        else { say('video tag present but no src yet'); }
      }
      // also check for explicit download button
      const dl = page.locator('a[download], button[aria-label*="Download"]').first();
      if (await dl.count() > 0) { say('download affordance found'); break; }
      await page.waitForTimeout(15000);
    }
    await shot(page, 'generated');
    if (!videoEl || await videoEl.count() === 0) {
      console.log(JSON.stringify({ ok: false, error: 'timeout_waiting_for_video', log }));
      await browser.close(); done(5);
    }

    // Download: prefer clicking a download button; fallback to fetching video src
    const fname = `gemini_${stamp()}.mp4`;
    const outPath = path.join(OUTDIR, fname);
    let downloaded = false;
    const dlBtn = page.locator('a[download], button[aria-label*="Download"]').first();
    if (await dlBtn.count() > 0) {
      try {
        const [download] = await Promise.all([
          page.waitForEvent('download', { timeout: 120000 }),
          dlBtn.click(),
        ]);
        await download.saveAs(outPath);
        downloaded = true;
        say('downloaded via button');
      } catch (e) { say('button download failed: ' + e.message); }
    }
    if (!downloaded) {
      const src = await videoEl.getAttribute('src');
      say('fetching video src directly');
      downloaded = await page.evaluate(async ({ src, outPath }) => {
        try {
          const r = await fetch(src);
          const buf = await r.arrayBuffer();
          const bytes = new Uint8Array(buf);
          // write via node not possible here; return base64 chunks is heavy — signal back
          return { bytes: bytes.length };
        } catch (e) { return { error: String(e) }; }
      }, { src, outPath }).then(async res => {
        if (res.error || !res.bytes) return false;
        // Do the actual fetch in Node instead (page context can't write files)
        return 'node_fetch_needed';
      });
      if (downloaded === 'node_fetch_needed') {
        // Get cookies from browser context and fetch in Node
        const cookies = await browser.cookies();
        const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join('; ');
        const https = require('https');
        const http = require('http');
        const finalUrl = src.startsWith('http') ? src : ('https://gemini.google.com' + src);
        await new Promise((resolve, reject) => {
          const mod = finalUrl.startsWith('https') ? https : http;
          mod.get(finalUrl, { headers: { 'Cookie': cookieHeader } }, resp => {
            if (resp.statusCode >= 300 && resp.statusCode < 400 && resp.headers.location) {
              // follow one redirect
              mod.get(resp.headers.location, { headers: { 'Cookie': cookieHeader } }, r2 => {
                const ws = fs.createWriteStream(outPath);
                r2.pipe(ws); ws.on('finish', resolve); ws.on('error', reject);
              }).on('error', reject);
            } else {
              const ws = fs.createWriteStream(outPath);
              resp.pipe(ws); ws.on('finish', resolve); ws.on('error', reject);
            }
          }).on('error', reject);
        });
        downloaded = true;
        say('downloaded via node fetch, src bytes path');
      }
    }

    const stat = fs.existsSync(outPath) ? fs.statSync(outPath) : null;
    await browser.close();
    if (stat && stat.size > 100000) {
      console.log(JSON.stringify({ ok: true, file: outPath, bytes: stat.size, log }));
    } else {
      console.log(JSON.stringify({ ok: false, error: 'download_failed_or_too_small', log }));
      done(6);
    }
  } catch (e) {
    try { await browser.close(); } catch (x) {}
    console.log(JSON.stringify({ ok: false, error: 'exception: ' + e.message }));
    done(1);
  }
})();
