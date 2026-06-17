# ytdl-dot-lol

A modern, simple, and easy-to-use web frontend for `yt-dlp`, built with Django and Celery.

This project is still in development.

You can see it working at: https://ytdl.lol

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
| **YouTube Daily Links** | 5 links per 24h per IP | YouTube URLs only |
| **Downloads per Hour** | 5 per IP | All platforms |
| **Video Duration** | 2 hours maximum | Per video |
| **Auto-Cleanup** | 1 hour after download | All downloaded files |
| **Large-File Cleanup** | >500MB removed after 30 min | All files |
| **Total Storage Cap** | ~100GB | Entire server |

### Why the YouTube daily limit?
YouTube aggressively rate-limits and can block IPs that perform too many extractions. The 5-links-per-day cap protects your server's IP reputation and keeps the service alive for everyone.

> **Note:** The daily limit is reset every 24 hours per IP. Other platforms (Reddit, etc.) are **not** affected by this limit.

---

## Run locally on Windows

```bash
python .\runserver-dev.py
```
```bash
python -m celery -A ytdl beat -l info
```
```bash
python -m celery -A ytdl worker --pool=solo
```

---

## License

This project is open-source (GPL-2.0-or-later). Feel free to contribute!
