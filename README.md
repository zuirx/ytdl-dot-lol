# ytdl-dot-lol
A modern, simple, and easy-to-use web frontend for `yt-dlp`, built with Django and Celery.

## Requirements
- **Python**: 3.11+
- **Redis**: Used as the broker and backend for Celery.
- **FFmpeg**: Required for audio conversion and mixing.
- **Celery**: For background task management.

## Installation
1. **Clone the repository**:
   ```bash
   git clone https://github.com/zuirx/ytdl-dot-lol && cd ytdl-dot-lol
   ```

2. **Set up Virtual Environment**:
   ```bash
   python -m venv venv
   # Linux/Mac:
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

6. **Install & Start Redis (Ubuntu Example)**:
   ```bash
   sudo apt update
   sudo apt install redis-server
   sudo systemctl enable redis-server
   sudo systemctl start redis-server
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

## Enforced Limits
To ensure site stability, the following limits are enforced:
-   **Video Duration**: Maximum 2 hours per video.
-   **Rate Limit**: 50 downloads per hour per IP.
-   **Cleanup**: Downloaded files are automatically deleted from the server 1 hour after creation.

## License
This project is open-source (GPL v2.1). Feel free to contribute!
