# ytdl-dot-lol

A modern, simple, and easy-to-use web frontend for `yt-dlp`, built with Django and Celery.

This project is still in development.

You can see it working at: https://ytdl.lol

---

## Features

- **Multi-Platform Support**: Download video and audio streams from YouTube, Reddit, and hundreds of other sites supported by `yt-dlp`.
- **Async Task Queue**: Celery & Redis pipeline with real-time progress percentage, status updates, and download cancellation.
- **Format Inspection & Mixing**: Pull available video/audio formats and manually mix selected streams with FFmpeg.
- **Regional Proxy Bypass**: Automatic detection of geo-restricted YouTube videos and automatic proxy cycling (SOCKS4/SOCKS5) using GeoIP-located proxies.
- **Age-Restriction & Bot Bypass**: Seamless Chrome cookie extraction and Node.js 22+ challenge solving for protected videos.
- **Admin Analytics Dashboard**: Track monthly unique visitors (IPs), page views, completed downloads, client devices/browsers, and top requested URLs.
- **Site Administration**: Set custom homepage warning notices and whitelist specific IPs from download limits.
- **Themes & Presets**: Dark and Light themes with persistent video/audio quality preferences.

---

## Requirements

- **Python**: 3.11+
- **Redis**: Used as the broker and backend for Celery.
- **FFmpeg**: Required for audio conversion and mixing.
- **Celery**: For background task management.
- **Node.js 22+**: **Required** for YouTube n-sig challenge solving (see [Node.js Setup](#nodejs-setup)).
- **Google Chrome**: **Required** with a logged-in Google account for YouTube cookie extraction (see [Chrome Setup](#chrome-setup)).

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/zuirx/ytdl-dot-lol && cd ytdl-dot-lol
   ```

2. **Set up Virtual Environment**:
   ```bash
   python -m venv venv
   # Linux/macOS:
   source venv/bin/activate
   # Windows:
   venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**:
   Create a `.env` file in the root directory:
   ```env
   SECRET_KEY="your-secure-secret-key"
   # Optional: override the Node.js path if not detected automatically
   # NODEJS_PATH="C:\Program Files\nodejs\node.exe"
   ```

5. **Prepare Static Files**:
   ```bash
   python manage.py collectstatic --noinput
   ```

6. **Install & Start Redis**:

   ### Linux (Ubuntu/Debian)
   ```bash
   sudo apt update
   sudo apt install redis-server
   sudo systemctl enable redis-server
   sudo systemctl start redis-server
   ```

   ### Windows (WSL2)
   Redis does not run natively on Windows. Install and run it inside WSL2:
   ```bash
   # From WSL2 Ubuntu/Debian
   sudo apt update
   sudo apt install redis-server
   sudo service redis-server start
   # or if systemd is available:
   sudo systemctl start redis-server
   ```

---

## Node.js Setup

**Node.js 22+ is mandatory.** yt-dlp uses it internally to solve JavaScript challenges (`n-sig`) required for almost all YouTube downloads.

### Linux (Ubuntu/Debian)
Using NodeSource:
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```
Or using [nvm](https://github.com/nvm-sh/nvm):
```bash
nvm install 22
nvm use 22
```

### Windows
Download the installer from [nodejs.org](https://nodejs.org/) (LTS 22.x) or use [nvm-windows](https://github.com/coreybutler/nvm-windows):
```powershell
nvm install 22
nvm use 22
```

### Automatic Detection
The application tries to detect Node.js automatically:
1. Uses `settings.NODEJS_WIN` (Windows) or `settings.NODEJS_LIN` (Linux) defaults.
2. Falls back to the `NODEJS_PATH` environment variable if set.
3. Falls back to whichever `node` is found in the system `PATH`.

If you installed Node.js in a custom location, set `NODEJS_PATH` in your `.env`.

---

## Chrome Setup

**Google Chrome with a signed-in Google account is strongly recommended.** The application extracts cookies directly from Chrome to authenticate YouTube requests. Without this, you will likely hit bot checks and age-restriction blocks.

### Steps
1. Install **Google Chrome** (Stable channel).
2. **Sign in** to your Google account inside Chrome.
3. Make sure Chrome is closed when the worker starts if you are on Windows (the cookie database locks while Chrome is running).
4. The app reads cookies from Chrome automatically — **no manual cookie export is required** in most cases.

### Manual Cookie Fallback (Optional)
If Chrome cookie extraction fails (e.g., on a headless server), you can place a `cookie.txt` in the project root. The app will fall back to it automatically.

To generate the file from a desktop machine:
```bash
yt-dlp --cookies-from-browser chrome --cookies "/path/to/ytdl-dot-lol/cookie.txt" "https://www.youtube.com"
```

Then copy `cookie.txt` to your server.

---

## Age-Restriction (YouTube)

In order to make age-restricted and bot-protected videos from YouTube to work, you will need:

1. **Node.js 22+** installed and available (see [Node.js Setup](#nodejs-setup)).
2. **Google Chrome** installed with a logged-in, age-verified Google account (see [Chrome Setup](#chrome-setup)).
3. The app automatically configures:
   - `player_client: web`
   - `js_runtimes: node`
   - `remote_components: ejs:github`
   - `cookiesfrombrowser: chrome`

No extra configuration is needed if Chrome and Node.js are properly installed.

---

## Regional Proxy Bypass (Geo-restriction)

The application includes an automated regional proxy system to bypass YouTube geo-restrictions:

1. **Country Detection**: The `/detect_region/?url=...` endpoint inspects restricted videos to find which regions they are allowed in.
2. **Aggregated Proxies**: Working proxies are fetched dynamically from multiple sources (Monosans, ProxyScrape, Geonode, TheSpeedX, Hookzof) and localized using `GeoLite2-City.mmdb`.
3. **Health Checking**: Live SOCKS4, SOCKS5, and HTTP proxies undergo fast concurrent TCP handshakes before being selected.
4. **Proxy Retry Cycle**: Celery worker retries downloads across tested regional proxies until the geo-restriction is successfully bypassed.
5. **Conditional UI Toggle**:
   - The proxy panel stays hidden by default for a clean interface.
   - Users can check *"This video may be blocked in my region"* to reveal region selection and custom proxy options.
   - If a download fails with a geo-block error, the UI automatically enables the bypass panel and triggers region detection.
   - Users can save preferred default bypass regions or custom proxies in the **Settings** panel.

---

## Running

You need to run three separate processes:

1. **Django Web Server**:
   ```bash
   python runserver-srv.py
   ```

2. **Celery Worker** (Handles downloads):
   ```bash
   celery -A ytdl worker -l info --pool=prefork --concurrency=4
   ```

3. **Celery Beat** (Handles scheduled tasks/updates):
   ```bash
   celery -A ytdl beat -l info
   ```

---

## Enforced Limits

To ensure site stability and avoid abuse, the following limits are enforced:

| Limit | Value | Scope |
|-------|-------|-------|
| **YouTube Daily Links** | 15 links per 24h per IP | YouTube URLs only (`YOUTUBE_DAILY_LIMIT` in settings) |
| **Downloads per Hour** | 5 downloads per IP | All platforms (rolling 1-hour cache window) |
| **Video Duration** | 2 hours maximum (7200s) | Per video (`MAX_VIDEO_DURATION` in settings) |
| **Periodic Content Cleanup** | Files older than 10 mins removed | Download, mix, and playlist directories (runs every 5 mins via Celery Beat) |
| **File Deletion Fallback** | 1 hour after download | In-memory cleanup loop |
| **Total Storage Cap** | ~100GB | Entire server storage (oldest files purged first if exceeded) |
| **IP Exemptions** | Configurable via Admin | Exclude specific IPs from YouTube daily and hourly rate limits |

### Why the YouTube daily limit?
YouTube aggressively rate-limits and can block IPs that perform too many extractions. The 15-links-per-day cap protects your server's IP reputation and keeps the service alive for everyone.

> **Note:** The daily limit is reset every 24 hours per IP. Other platforms (Reddit, etc.) are **not** affected by this YouTube daily limit. Specific IPs can be exempted in Django Admin under **Site Settings** (`youtube_limit_exception_ips`).

---

## Admin Analytics Dashboard

A built-in analytics dashboard is available in Django Admin at `/admin/main/analyticsdashboard/`:

- **Monthly Performance**: Unique visitors (IPs), total page views, and completed downloads.
- **Top Visited Pages**: Most accessed URLs and referrers.
- **Top Visitors**: Request volume by client IP.
- **Device & Browser Breakdown**: Client breakdown across Desktop, Mobile/Tablet, and Bots.

---

## Run locally on Windows

### Option 1: One-Click Development Runner (Recommended)

Start Redis (in WSL Debian/Ubuntu), Django dev server, Celery Beat, and Celery Worker together:

```cmd
.\start-dev.bat
```

*Or using PowerShell:*
```powershell
.\start-dev.ps1
```

*Or using Python:*
```bash
python .\start-dev.py
```

### Option 2: Manual Multi-Process Setup

If running each service in a separate terminal:

1. **Redis in WSL**:
   ```bash
   wsl -d debian redis-server
   ```
2. **Django Web Server**:
   ```bash
   python .\runserver-dev.py
   ```
3. **Celery Beat**:
   ```bash
   python -m celery -A ytdl beat -l info
   ```
4. **Celery Worker**:
   ```bash
   python -m celery -A ytdl worker --pool=solo
   ```

---

## License

This project is open-source (GPL-2.0-or-later). Feel free to contribute!
