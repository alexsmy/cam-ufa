"""Обновление превью камер и сборка index.html.

Скачивает по одному кадру с каждой камеры (4 потока), сохраняет в previews/
и собирает страницу из cams.json + template.html.

Запуск:
    python refresh.py                # скачать превью и собрать страницу
    python refresh.py --no-download  # только собрать (для проверки вёрстки)
"""
import argparse
import concurrent.futures
import datetime
import gc
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

MAX_MP4 = 2 * 1024 * 1024
UA = 'Mozilla/5.0'
WORKERS = 4
RETRIES = 2
IMG_WIDTH = 480

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = 'ffmpeg'

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Asia/Yekaterinburg')   # Уфа, UTC+5
except Exception:
    TZ = datetime.timezone(datetime.timedelta(hours=5))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def detect_ext():
    """WebP, если ffmpeg умеет его кодировать, иначе JPEG."""
    try:
        out = subprocess.run([FFMPEG, '-hide_banner', '-encoders'],
                             capture_output=True, timeout=20).stdout.decode('utf-8', 'ignore')
        if 'libwebp' in out:
            return 'webp'
    except Exception:
        pass
    return 'jpg'


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


def grab(cid, tmpdir, ext):
    """Возвращает True, если превью успешно обновлено."""
    host, token = get_token_and_host(cid)
    if not host or not token:
        return False
    mp4 = os.path.join(tmpdir, cid + '.mp4')
    out = os.path.join(tmpdir, cid + '.' + ext)
    url = f'https://{host}/{cid}/preview.mp4?token={token}'
    try:
        if not download_limited(url, mp4):
            return False
        if ext == 'webp':
            codec = ['-c:v', 'libwebp', '-quality', '72', '-compression_level', '4']
        else:
            codec = ['-q:v', '4']
        r = subprocess.run([
            FFMPEG, '-y', '-nostdin', '-loglevel', 'error',
            '-threads', '1', '-filter_threads', '1',
            '-i', mp4, '-frames:v', '1', '-vf', f'scale={IMG_WIDTH}:-2',
            *codec, out,
        ], capture_output=True, timeout=40)
        if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
            return False
        dst = os.path.join('previews', cid + '.' + ext)
        shutil.move(out, dst)
        return True
    except Exception:
        return False
    finally:
        for p in (mp4, out):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def render(cams, ok, ext, now):
    stamp = now.strftime('%d.%m.%Y %H:%M')
    hour = now.strftime('%Y%m%d%H')
    sections, cur, buf = [], None, []

    def flush():
        if cur is None:
            return
        items = ''.join(buf)
        sections.append(
            '<section class="jk"><h2>%s <span class="cnt">%d</span></h2>'
            '<div class="cams">%s</div></section>' % (html.escape(cur), len(buf), items))

    for c in cams:
        cid, jk = c['id'], c.get('jk', '')
        title = c.get('title') or jk
        if jk != cur:
            flush()
            cur, buf = jk, []
        stream = f'https://ucams.ufanet.ru/api/internal/embed/{cid}/?ttl=3600&autoplay=true&mute=true'
        src = f'previews/{cid}.{ext}?v={hour}'
        alt = html.escape(f'{jk}, {title}')
        search = html.escape(f'{jk} {title} {cid}'.lower(), quote=True)
        has_img = os.path.exists(os.path.join('previews', cid + '.' + ext))
        ts = now.strftime('%H:%M') if ok.get(cid) else ''
        if ok.get(cid):
            badge = ''
        elif has_img:
            badge = '<span class="badge">нет сигнала</span>'
        else:
            badge = '<span class="badge">нет данных</span>'
        if has_img:
            shot = (f'<a class="shot" href="{stream}" target="_blank" rel="noopener noreferrer">'
                    f'<img src="{src}" alt="{alt}" width="480" height="270" loading="lazy" decoding="async">'
                    f'{badge}</a>')
        else:
            shot = (f'<a class="shot" href="{stream}" target="_blank" rel="noopener noreferrer">'
                    f'<div style="aspect-ratio:16/9;background:var(--line);border-radius:10px 10px 0 0"></div>'
                    f'{badge}</a>')
        buf.append(
            f'<article class="cam" data-id="{cid}" data-search="{search}">'
            f'{shot}<div class="cap"><a class="t" href="{stream}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(title)}</a>'
            f'<span class="ts">{ts}</span>'
            f'<button class="star" type="button" aria-label="В избранное" aria-pressed="false">☆</button>'
            f'</div></article>')
    flush()

    tpl = open('template.html', encoding='utf-8').read()
    built = int(now.timestamp() * 1000)
    page = (tpl.replace('{{SECTIONS}}', '\n'.join(sections))
               .replace('{{COUNT}}', str(len(cams)))
               .replace('{{ONLINE}}', str(sum(1 for v in ok.values() if v)))
               .replace('{{STAMP}}', stamp)
               .replace('{{BUILT_MS}}', str(built)))
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(page)
    return len(page)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-download', action='store_true', help='не скачивать, только собрать страницу')
    args = ap.parse_args()

    cams = json.load(open('cams.json', encoding='utf-8'))
    os.makedirs('previews', exist_ok=True)
    ext = detect_ext()
    now = datetime.datetime.now(TZ)
    ok = {}

    if args.no_download:
        ok = {c['id']: True for c in cams}
    else:
        tmpdir = tempfile.mkdtemp(prefix='cam_')
        try:
            def job(c):
                for attempt in range(RETRIES + 1):
                    if grab(c['id'], tmpdir, ext):
                        return c['id'], True
                    time.sleep(0.5 * (attempt + 1))
                return c['id'], False

            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for i, (cid, good) in enumerate(pool.map(job, cams), 1):
                    ok[cid] = good
                    if i % 10 == 0:
                        print(f'{i}/{len(cams)}', flush=True)
                        gc.collect()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # удаляем превью камер, которых больше нет в cams.json
    alive = {c['id'] for c in cams}
    for name in os.listdir('previews'):
        cid = os.path.splitext(name)[0]
        if cid not in alive:
            os.remove(os.path.join('previews', name))

    size = render(cams, ok, ext, now)
    good = sum(1 for v in ok.values() if v)
    print(f'OK {good}/{len(cams)}; формат {ext}; index.html {size // 1024} КБ')
    if not args.no_download and good == 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
