from urllib.request import Request, urlopen
import ssl
import re

url = 'https://open.spotify.com/track/4UBWugj5D20ZveyzdGpqvD'
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
            print('Spotify og:image:', img_url)
        else:
            print('No og:image found')
except Exception as e:
    print(f'Error: {e}')
