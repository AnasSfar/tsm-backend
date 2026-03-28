# Taylor Swift Music Data Backend

A data collection and export pipeline for Taylor Swift music data from multiple sources (Apple Music, Spotify, Billboard) with cloud storage integration.

## 🎯 Features

- **Apple Music Collector**: Top songs, genres, country charts, and albums (with detailed metadata)
- **Spotify Collector**: Streaming history, charts (global & France), album milestones
- **Billboard Collector**: Hot 100, Billboard 200, TS chart history
- **Cloud Export**: Automatic upload to Cloudflare R2
- **Web Preview**: Static site generation with historical data

## 📋 Requirements

- Python 3.11+
- Playwright (browser automation)
- Boto3 (AWS S3/R2 compatible)
- Requests (HTTP)
- Cloudflare account with R2 bucket (optional)

## 🚀 Quick Start

### 1. Clone & Setup

```bash
git clone https://github.com/AnasSfar/tsm-backend.git
cd tsm-backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Cloudflare R2 (optional, for cloud export)
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET=your_bucket

# Enable R2 upload after collection
UPLOAD_TO_R2=1

# Optional: Collector tuning
APPLE_MUSIC_COUNTRIES=fr,us,gb,de,au
APPLE_MUSIC_TIMEOUT=20
```

### 4. Run Collectors

**Apple Music** (all sources):
```bash
python collectors/apple_music/run_apple_music.py
```

**Individual sources:**
```bash
python collectors/apple_music/ts_page.py fr
python collectors/apple_music/global.py
python collectors/apple_music/genre_charts.py
python collectors/apple_music/country_charts.py
python collectors/apple_music/country_albums.py
```

**Spotify Streams**:
```bash
cd collectors/spotify/streams
python update_streams.py
```

**Spotify Charts** (Global & France):
```bash
cd collectors/spotify/charts/global
python daily.py

cd ../fr
python daily.py
```

**Billboard**:
```bash
python collectors/billboard/scrape_billboard.py
```

### 5. Export Data

After collection, export to JSON/CSV:

```bash
# Apple Music export
python scripts/export_apple_music.py

# Spotify R2 export (if UPLOAD_TO_R2=1)
python scripts/r2.py
```

## 📊 Data Flow

```
Collectors                  Export                    Cloud
├─ Apple Music ─┐
├─ Spotify      ├─→ CSV/JSON ─→ Export Scripts ─→ R2 (optional)
├─ Billboard ───┤
└─ Local Tracks─┘
                        Website
                    (website/site/data/)
```

## 📁 Project Structure

```
tsm-backend/
├── collectors/
│   ├── apple_music/     # Song charts, global, genres, countries, albums
│   ├── spotify/         # Streams history, charts (global & FR)
│   └── billboard/       # Hot 100, BB 200, TS history
├── scripts/
│   ├── export_apple_music.py   # Generate JSON exports
│   ├── r2.py                   # Upload to Cloudflare R2
│   └── upload_ap_r2.py         # Apple Music specific upload
├── db/                  # CSV history files
├── website/             # Static site with JSON data
└── docs/               # Documentation
```

## 🔑 Configuration Reference

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `R2_ACCOUNT_ID` | - | Cloudflare R2 account ID |
| `R2_ACCESS_KEY_ID` | - | R2 access key |
| `R2_SECRET_ACCESS_KEY` | - | R2 secret key |
| `R2_BUCKET` | `taylor-data` | R2 bucket name |
| `UPLOAD_TO_R2` | `0` | Enable auto-upload (0=off, 1=on) |
| `APPLE_MUSIC_COUNTRIES` | `fr,us,gb,de,au,ca,jp,...` | Country codes to collect |
| `APPLE_MUSIC_TIMEOUT` | `20` | HTTP timeout (seconds) |
| `APPLE_MUSIC_RETRY_TOTAL` | `3` | HTTP retry attempts |
| `APPLE_MUSIC_RETRY_BACKOFF` | `1.0` | HTTP retry backoff |

## 🧪 Testing

Run Apple Music unit tests:

```bash
python -m unittest discover -s collectors/apple_music/tests -p "test_*.py"
```

Or with custom config:

```powershell
$env:APPLE_MUSIC_TIMEOUT = "30"
python -m unittest discover -s collectors/apple_music/tests -p "test_*.py"
```

## 🔍 Apple Music Collector Details

- **Sources**: MusicKit API (charts, genres) + RSS feeds (country charts, albums)
- **Metadata**: Song name, ID, duration, release date, ISRC, content rating, genres
- **Countries**: Configurable via `APPLE_MUSIC_COUNTRIES`
- **Token**: Auto-cached from Apple Music web app
- **Update frequency**: Daily (charts, rankings)

See [collectors/apple_music/README.md](collectors/apple_music/README.md) for more.

## ☁️ Cloud Integration (R2)

### Setup

1. Create Cloudflare R2 bucket
2. Generate API tokens
3. Add to `.env`
4. Set `UPLOAD_TO_R2=1`

### What Gets Uploaded

- Apple Music: `apple-music/history-by-song/{song_id}.json`
- Spotify: `history-by-track/{track_id}.json` (550+ files)
- Static: `data/*.json`, `history/*.json` (30+ files)

### Example

```bash
# Automatic upload after collection
UPLOAD_TO_R2=1 python collectors/apple_music/run_apple_music.py

# Manual upload
python scripts/r2.py --bucket your-bucket
```

## 🛠️ Development

### Code Style

Python 3.11+ with type hints preferred.

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Format & lint
black collectors/ scripts/
ruff check --fix collectors/ scripts/
```

### CI/CD

GitHub Actions workflow: [.github/workflows/apple-music-tests.yml](.github/workflows/apple-music-tests.yml)

Runs Apple Music unit tests on push to main.

## 📄 License

MIT - See [LICENSE](LICENSE) file

## 👤 Author

Created for Taylor Swift music data tracking project.

## 🐛 Issues & Contributions

Bug reports and PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

## 📚 Additional Resources

- [Deployment Audit](DEPLOYMENT_AUDIT.md) - Pre-launch security checklist
- [Apple Music Collector Docs](collectors/apple_music/README.md)
- [Spotify Streams Docs](collectors/spotify/streams/README.md)
