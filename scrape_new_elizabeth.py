from urllib.request import Request, urlopen
import ssl
import re

url = 'https://open.spotify.com/intl-fr/track/3AKV7Mvo2Mx4tb39iPvPlT'
try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=10, context=ctx) as resp:
        html = resp.read().decode('utf-8', errors='replace')
        match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if match:
            img_url = match.group(1)
            print('New Spotify image:')
            print(img_url)
except Exception as e:
    print(f'Error: {e}')
