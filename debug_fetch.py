import urllib.request, urllib.error
cid = '1772107008EYG20'
url = f'https://ucams.ufanet.ru/api/internal/embed/{cid}/?ttl=3600'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    r = urllib.request.urlopen(req, timeout=15)
    print('STATUS', r.status, 'HEADERS', dict(r.headers))
except urllib.error.HTTPError as e:
    print('HTTPERROR', e.code)
    print('LOCATION', e.headers.get('Location'))
    print('BODY', e.read()[:300])
except Exception as e:
    print('EXC', repr(e))
