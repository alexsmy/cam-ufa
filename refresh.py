"""Обновление index.html со встроенными превью — 4 потока.

На GitHub 7 ГБ RAM, поэтому 4 параллельных воркера безопасно (~200 МБ пик).
Каждый ffmpeg всё равно в 1 поток. Временные файлы уникальны на камеру.
"""
import os, re, subprocess, time, gc, base64, urllib.request, urllib.error, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_MP4 = 2 * 1024 * 1024
UA = 'Mozilla/5.0'
WORKERS = 4


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get_token_and_host(cid):
    url = f'https://ucams.ufanet.ru/api/internal/embed/{cid}/?ttl=3600&autoplay=true&mute=true'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        urllib.request.build_opener(NoRedirect).open(req, timeout=15)
        return None, None
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location', '')
    except Exception:
        return None, None
    h = re.match(r'https?://([^/]+)', loc or '')
    t = re.search(r'[?&]token=([^&]+)', loc or '')
    return (h.group(1) if h else None), (t.group(1) if t else None)


def download_limited(url, path):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    total = 0
    with urllib.request.urlopen(req, timeout=20) as r, open(path, 'wb') as f:
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_MP4:
                return False
            f.write(chunk)
    return total > 1000


def jpeg_for(cid, tmpdir):
    host, token = get_token_and_host(cid)
    if not host or not token:
        return None
    mp4 = os.path.join(tmpdir, f'_cam_{cid}.mp4')
    jpg = os.path.join(tmpdir, f'_cam_{cid}.jpg')
    url = f'https://{host}/{cid}/preview.mp4?token={token}'
    try:
        if not download_limited(url, mp4):
            return None
        subprocess.run([
            'ffmpeg', '-y', '-nostdin', '-loglevel', 'error',
            '-threads', '1', '-filter_threads', '1',
            '-i', mp4, '-frames:v', '1', '-vf', 'scale=320:-2',
            '-q:v', '5', jpg
        ], capture_output=True, timeout=30)
        if os.path.exists(jpg) and os.path.getsize(jpg) > 1000:
            return open(jpg, 'rb').read()
    except Exception:
        pass
    finally:
        for p in (mp4, jpg):
            if os.path.exists(p):
                os.remove(p)
    return None


if __name__ == '__main__':
    tmpdir = tempfile.mkdtemp(prefix='cam_')
    result = {}
    try:
        ids = [l.strip() for l in open('ids.json') if l.strip()]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(jpeg_for, cid, tmpdir): cid for cid in ids}
            for i, f in enumerate(as_completed(futs), 1):
                cid = futs[f]
                result[cid] = f.result()
                if i % 10 == 0:
                    print(f'{i}/{len(ids)}', flush=True)
                    gc.collect()

        template = open('index.template.html').read()

        def repl(m):
            data = result.get(m.group(1))
            return m.group(0) if not data else \
                'src="data:image/jpeg;base64,' + base64.b64encode(data).decode() + '"'

        html, n = re.subn(r'src="previews_jpg/([A-Za-z0-9_-]+)\.jpg"', repl, template)
        open('index.html', 'w').write(html)
        print(f'OK {sum(1 for v in result.values() if v)}/{len(ids)}; embedded {n}; html {len(html)//1024} KB')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
