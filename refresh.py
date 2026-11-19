"""Обновление index.html со встроенными превью — 1 поток (надёжно).

Последовательно, один ffmpeg, потоковая загрузка с лимитом. Память ~40-60 МБ.
"""
import os, re, subprocess, time, gc, base64, urllib.request, urllib.error, tempfile, shutil, datetime

MAX_MP4 = 2 * 1024 * 1024
UA = 'Mozilla/5.0'
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = 'ffmpeg'


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
        if os.environ.get('DEBUG'):
            print('DBG', cid, 'HTTP', e.code, 'Location?', bool(loc), flush=True)
    except Exception as ex:
        if os.environ.get('DEBUG'):
            print('DBG', cid, 'EXC', repr(ex)[:120], flush=True)
        return None, None
    h = re.match(r'https?://([^/]+)', loc or '')
    t = re.search(r'[?&]token=([^&]+)', loc or '')
    host, token = (h.group(1) if h else None), (t.group(1) if t else None)
    if os.environ.get('DEBUG'):
        print('DBG token', cid, bool(host), bool(token), flush=True)
    return host, token


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
    mp4 = os.path.join(tmpdir, '_cam.mp4')
    jpg = os.path.join(tmpdir, f'{cid}.jpg')
    url = f'https://{host}/{cid}/preview.mp4?token={token}'
    try:
        if not download_limited(url, mp4):
            if os.environ.get('DEBUG'):
                print('DBG download FAIL', cid, os.path.getsize(mp4) if os.path.exists(mp4) else 0, flush=True)
            return None
        if os.environ.get('DEBUG'):
            print('DBG mp4', cid, os.path.getsize(mp4), flush=True)
        r = subprocess.run([
            FFMPEG, '-y', '-nostdin', '-loglevel', 'error',
            '-threads', '1', '-filter_threads', '1',
            '-i', mp4, '-frames:v', '1', '-vf', 'scale=320:-2',
            '-q:v', '5', jpg
        ], capture_output=True, timeout=30)
        if os.environ.get('DEBUG'):
            print('DBG ffmpeg', cid, 'rc', r.returncode, 'jpg',
                  os.path.getsize(jpg) if os.path.exists(jpg) else 0,
                  r.stderr.decode()[:150], flush=True)
        if os.path.exists(jpg) and os.path.getsize(jpg) > 1000:
            return open(jpg, 'rb').read()
    except Exception as ex:
        if os.environ.get('DEBUG'):
            print('DBG EXC', cid, repr(ex)[:150], flush=True)
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
        for i, cid in enumerate(ids, 1):
            result[cid] = jpeg_for(cid, tmpdir)
            if i % 10 == 0:
                print(f'{i}/{len(ids)}', flush=True)
                gc.collect()
            time.sleep(0.05)

        template = open('index.template.html').read()

        def repl(m):
            data = result.get(m.group(1))
            return m.group(0) if not data else \
                'src="data:image/jpeg;base64,' + base64.b64encode(data).decode() + '"'

        html, n = re.subn(r'src="previews_jpg/([A-Za-z0-9_-]+)\.jpg"', repl, template)
        stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime('%d.%m.%Y %H:%M')
        html = re.sub(r'собрано \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}', 'собрано ' + stamp, html)
        open('index.html', 'w').write(html)
        print(f'OK {sum(1 for v in result.values() if v)}/{len(ids)}; embedded {n}; html {len(html)//1024} KB')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
