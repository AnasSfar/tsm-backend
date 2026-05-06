import requests, json
from pathlib import Path

session_file = Path("collectors/spotify/streams/tools/.session.json")
cookies = {}
if session_file.exists():
    for c in json.loads(session_file.read_text())["cookies"]:
        cookies[c["name"]] = c["value"]

r = requests.get(
    "https://open.spotify.com/get_access_token",
    params={"reason": "transport", "productType": "web_player"},
    cookies=cookies,
    timeout=10,
)
print(r.status_code, r.text[:300])
