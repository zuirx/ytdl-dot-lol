# ytdl-dot-lol

A modern, simple, and easy-to-use web frontend for `yt-dlp`, built with Django and Celery.

This project is still in development.

You can see it working at: https://ytdl.lol

## Requirements

- **Python**: 3.11+
- **Redis**: Used as the broker and backend for Celery.
- **FFmpeg**: Required for audio conversion and mixing.
- **Celery**: For background task management.
- **Node.js 22+**: Required for YouTube age-restricted content and n-sig challenge solving.

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

7. **Install Node.js 22+**:

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

## Age-Restriction (YouTube)

In order to make age restriction from YouTube to work, you will need:

1. **Node.js 22+** installed and available in your system's `PATH`. yt-dlp uses it to solve JavaScript challenges (n-sig) required for age-restricted videos.
2. A valid **cookie file** from an age-verified YouTube account.

### Getting your cookie file

- Make sure you have `yt-dlp` CLI installed.
- Log in to YouTube (with age verified) in Chrome.
- Close Chrome completely (so the cookie database is not locked).
- Save your `cookie.txt` with the command:

```bash
yt-dlp --cookies-from-browser chrome --cookies "/path/to/ytdl-dot-lol/cookie.txt" "https://www.youtube.com"
```

Place the `cookie.txt` in the root directory of this project.

## Enforced Limits

To ensure site stability, the following limits are enforced:
- **Video Duration**: Maximum 1 hour per video.
- **Rate Limit**: 5 downloads per hour per IP.
- **Cleanup**: Downloaded files are automatically deleted from the server 1 hour after creation.

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

## License

This project is open-source (GPL-2.0-or-later). Feel free to contribute!
