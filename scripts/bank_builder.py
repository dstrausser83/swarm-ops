#!/usr/bin/env python3
"""bank_builder.py — build a 3-steps-ahead meme image bank on the operator PC (free, browser Gemini).

Reads media/bank/PROMPTS.txt (pillar|prompt), generates one image per prompt via the
PC's gemini_image.js (serialized by the browser.lock — one job at a time, PC stays snappy),
retrieves each image, runs basic QC (file size, dimensions), and registers it in
media/bank/bank.json with a use-count. The drafter pulls from the bank first so
drafting never waits on image generation.

Usage: python3 bank_builder.py [--limit N]
"""
import json, os, subprocess, sys, time, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, 'media', 'bank')
PROMPTS = os.path.join(BANK, 'PROMPTS.txt')
MANIFEST = os.path.join(BANK, 'bank.json')
PC_SSH = os.path.expanduser('~/workspace/bin/pc-ssh')  # local helper; see README
PC_SSH_HOST = os.environ.get('PC_SSH_HOST', 'rockpc')  # local SSH alias for the PC
SSH_CONFIG = os.path.expanduser('~/.ssh/config')  # local SSH config with the PC host entry
PC_SCRIPT = 'C:/RockVideos/work/gemini_image.js'
PC_OUT = 'C:/RockVideos/outbox/images'

def run(cmd, timeout=300):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def load_manifest():
    if os.path.exists(MANIFEST):
        return json.load(open(MANIFEST))
    return {"images": []}

def save_manifest(m):
    json.dump(m, open(MANIFEST, 'w'), indent=2)

def main():
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else 999
    manifest = load_manifest()
    have_prompts = {im['prompt'] for im in manifest['images']}
    jobs = []
    for line in open(PROMPTS):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        pillar, prompt = line.split('|', 1)
        if prompt in have_prompts:
            continue
        jobs.append((pillar.strip(), prompt.strip()))
    jobs = jobs[:limit]
    print(f"[bank] {len(jobs)} images to generate", flush=True)
    made = 0
    for pillar, prompt in jobs:
        # The PC script enforces the one-browser-job lock itself; retry once on pc_busy.
        result_path = None
        for attempt in range(2):
            ps = ('powershell -NoProfile -Command "Set-Location C:/RockVideos/work; '
                  f'node gemini_image.js \\"{prompt}\\" {PC_OUT}"')
            rc, out, err = run([PC_SSH, ps], timeout=600)
            tail = (out.strip().split('\n')[-1] if out.strip() else '')
            try:
                res = json.loads(tail[tail.index('{'):])
            except Exception:
                res = {}
            if res.get('ok'):
                result_path = res['file']
                break
            if res.get('error') == 'pc_busy':
                print(f"[bank] pc busy, waiting 60s then retry", flush=True)
                time.sleep(60)
                continue
            print(f"[bank] FAILED attempt {attempt+1}: {prompt[:60]} -> {res.get('error')}", flush=True)
            break
        if not result_path:
            continue
        fname = f"bank_{pillar}_{int(time.time())}_{made}.jpg"
        local = os.path.join(BANK, fname)
        win_path = result_path.replace('\\', '/')
        rc, out, err = run(['scp', '-F', os.path.expanduser('~/.ssh/config'), '-q',
                            f'{PC_SSH_HOST}:{win_path}', local], timeout=120)
        if rc != 0 or not os.path.exists(local) or os.path.getsize(local) < 20000:
            print(f"[bank] retrieve failed: {prompt[:60]}", flush=True)
            continue
        manifest['images'].append({
            'file': fname, 'pillar': pillar, 'prompt': prompt,
            'bytes': os.path.getsize(local), 'uses': 0,
            'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        })
        save_manifest(manifest)
        made += 1
        print(f"[bank] {made}/{len(jobs)} banked: {fname} ({pillar})", flush=True)
    print(f"[bank] done — {made} new images, {len(manifest['images'])} total in bank", flush=True)
    # sync inventory to the PC so the bank-keeper agents can see stock levels
    subprocess.run(['scp', '-F', os.path.expanduser('~/.ssh/config'), '-q',
                    MANIFEST, f'{PC_SSH_HOST}:C:/RockVideos/work/bank.json'],
                   capture_output=True, timeout=60)

if __name__ == '__main__':
    main()
