import os
import yt_dlp
import logging
import zipfile
import shutil
import subprocess
import glob
from celery import shared_task
from django.conf import settings
import time

logger = logging.getLogger(__name__)

COOKIES_PATH = os.path.abspath(os.path.join(settings.BASE_DIR, 'cookie.txt'))

# Default yt-dlp options applied to every YouTube extraction/download
YTDL_OPTS = {
    'verbose': True,
    'js_runtimes': {'node': {'path': settings.NODEJS}},
    'extractor_args': {'youtube': {'player_client': ['web']}},
    'remote_components': {'ejs:github'},
    'cookiesfrombrowser': ('chrome', None, None, None),
}

def _is_youtube(url):
    return 'youtube.com' in url or 'youtu.be' in url

def _age_restricted_error(exc):
    msg = str(exc).lower()
    return any(p in msg for p in ('sign in to confirm your age', 'confirm your age', 'age restriction', 'this video may be inappropriate'))

def _auth_error(exc):
    """Detect auth/bot/age-restriction failures that may be fixed with different cookies."""
    msg = str(exc).lower()
    return any(p in msg for p in (
        'sign in to confirm',
        'not a bot',
        'use --cookies-from-browser or --cookies',
        'confirm your age',
        'age restriction',
        'this video may be inappropriate',
        'requested format is not available',
    ))

def _format_not_available_error(exc):
    msg = str(exc).lower()
    return 'requested format is not available' in msg

def _extract_with_cookie_fallback(url, opts, download=False):
    """Run yt-dlp via Python library with YouTube defaults + cookies.

    Tries strategies in order:
      1) Merged opts (YTDL_OPTS + caller opts) – uses browser cookies by default.
      2) cookie.txt file if present and non-empty.
      3) Force browser cookies for age-restricted videos when cookiefile fails.
    """
    if _is_youtube(url):
        merged = YTDL_OPTS.copy()
        merged.update(opts)
        opts = merged

    tried_cookiefile = False
    tried_browser = bool(opts.get('cookiesfrombrowser'))

    # ---- Strategy 1: whatever came in (usually browser cookies from YTDL_OPTS) ----
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download)
    except yt_dlp.utils.DownloadError as exc:
        last_exc = exc

        # ---- Strategy 2: fallback to cookie.txt ----
        if _auth_error(exc) and not tried_cookiefile:
            if os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0:
                logger.warning('Browser cookies failed for %s, trying cookie.txt', url)
                tried_cookiefile = True
                cookie_opts = opts.copy()
                cookie_opts.pop('cookiesfrombrowser', None)
                cookie_opts['cookiefile'] = COOKIES_PATH
                try:
                    with yt_dlp.YoutubeDL(cookie_opts) as ydl:
                        return ydl.extract_info(url, download=download)
                except yt_dlp.utils.DownloadError as exc2:
                    last_exc = exc2

        # ---- Strategy 3: age-restricted + cookiefile failed -> force browser cookies ----
        if _age_restricted_error(last_exc) and tried_cookiefile and not tried_browser:
            logger.warning('cookie.txt failed for age-restricted %s, forcing browser cookies', url)
            tried_browser = True
            browser_opts = opts.copy()
            browser_opts.pop('cookiefile', None)
            browser_opts['cookiesfrombrowser'] = ('chrome', None, None, None)
            try:
                with yt_dlp.YoutubeDL(browser_opts) as ydl:
                    return ydl.extract_info(url, download=download)
            except yt_dlp.utils.DownloadError as exc3:
                last_exc = exc3

        raise last_exc

# Constants are now in settings.py

@shared_task
def cleanup_large_files_task():
    """
    Routine to remove very large files from content directories.
    - Files > 500MB are removed if older than 30 minutes.
    - Total storage limit of 100GB is enforced by removing oldest files.
    """
    dirs_to_clean = [settings.DIR_DOWNLOAD, settings.DIR_MIX, settings.DIR_PLAYLIST]
    LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500 MB
    TIME_THRESHOLD = 30 * 60  # 30 minutes
    TOTAL_STORAGE_LIMIT = 100 * 1024 * 1024 * 1024  # 100 GB
    
    now = time.time()
    all_files = []
    current_total_size = 0

    for d in dirs_to_clean:
        if not os.path.exists(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                path = os.path.join(root, f)
                try:
                    stat = os.stat(path)
                    size = stat.st_size
                    mtime = stat.st_mtime
                    
                    # Rule 1: Remove very large files older than 30 mins
                    if size > LARGE_FILE_THRESHOLD and (now - mtime) > TIME_THRESHOLD:
                        os.remove(path)
                        logger.warning(f"Cleanup: Removed large file {path} (>500MB and >30m old)")
                        continue
                    
                    all_files.append((path, mtime, size))
                    current_total_size += size
                except OSError:
                    pass

    # Rule 2: Enforce total storage limit of 100GB
    if current_total_size > TOTAL_STORAGE_LIMIT:
        logger.info(f"Cleanup: Total size {current_total_size} exceeds limit {TOTAL_STORAGE_LIMIT}. Purging oldest files.")
        # Sort by mtime ascending (oldest first)
        all_files.sort(key=lambda x: x[1])
        
        for path, mtime, size in all_files:
            try:
                os.remove(path)
                current_total_size -= size
                if current_total_size <= TOTAL_STORAGE_LIMIT:
                    break
                logger.warning(f"Cleanup: Purged old file {path} to free space")
            except OSError:
                pass

def zip_folder_task(folder_path, output_path):
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname   = os.path.relpath(file_path, os.path.dirname(folder_path))
                zipf.write(file_path, arcname)

def get_format_for_quality(type, quality):
    if type == 'video':
        match quality:
            case 'worst':
                return 'worstvideo+worstaudio/worst'
            case 'low':
                return 'bestvideo[height<=360]+bestaudio/best[height<=360]'
            case 'medium':
                return 'bestvideo[height<=480]+bestaudio/best[height<=480]'
            case 'high':
                return 'bestvideo[height<=720]+bestaudio/best[height<=720]'
            case _:
                return 'bestvideo+bestaudio/best'
    else: # audio
        match quality:
            case 'worst':
                return 'worstaudio/worst'
            case 'low':
                return 'bestaudio[abr<=64]/best'
            case 'medium':
                return 'bestaudio[abr<=128]/best'
            case 'high':
                return 'bestaudio[abr<=192]/best'
            case _:
                return 'bestaudio/best'

def _download_reddit_best_video(url, output_dir, video_id):
    os.makedirs(output_dir, exist_ok=True)

    video_template = os.path.join(output_dir, f'{video_id}_video.%(ext)s')
    audio_template = os.path.join(output_dir, f'{video_id}_audio.%(ext)s')

    def _download_stream(format_selector, outtmpl):
        opts = {
            'quiet': True,
            'format': format_selector,
            'outtmpl': outtmpl,
        }
        _extract_with_cookie_fallback(url, opts, download=True)
        # Find the downloaded file by globbing the template stem
        stem = outtmpl.replace('%(ext)s', '*')
        files = glob.glob(stem)
        if files:
            return max(files, key=os.path.getmtime)
        raise RuntimeError('Could not determine downloaded file path for Reddit stream.')

    # Use 'bestvideo' and 'bestaudio' instead of hardcoded itags
    video_file = _download_stream('bestvideo', video_template)
    audio_file = _download_stream('bestaudio', audio_template)
    final_path = os.path.join(output_dir, f'{video_id}.mp4')

    command = [
        'ffmpeg', '-y',
        '-i', video_file,
        '-i', audio_file,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0',
        final_path,
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg error: {e.stderr or e.stdout}')

    return final_path

@shared_task(bind=True)
def download_playlist_task(self, video_urls, download_type='audio', quality='best', output_dir='content-playlist'):
    os.makedirs(output_dir, exist_ok=True)
    task_id = self.request.id
    pl_temp_dir = os.path.join(output_dir, task_id)
    os.makedirs(pl_temp_dir, exist_ok=True)
    
    total_vids = len(video_urls)
    
    if download_type == 'audio':
        params = {
            'format':  get_format_for_quality('audio', quality),
            'postprocessors': [{
                'key':              'FFmpegExtractAudio',
                'preferredcodec':   'mp3',
                'preferredquality': '192',
            }],
            'outtmpl':            f'{pl_temp_dir}/%(title)s.%(ext)s',
            'quiet':              True,
            'nocheckcertificate': True,
        }
    else:
        params = {
            'format':              get_format_for_quality('video', quality),
            'merge_output_format': 'mp4',
            'outtmpl':             f'{pl_temp_dir}/%(title)s.%(ext)s',
            'quiet':               True,
            'nocheckcertificate':  True,
        }

    for i, v_url in enumerate(video_urls):
        current = i + 1
        self.update_state(state='PROGRESS', meta={
            'percent': (i / total_vids) * 100,
            'status': f'Downloading video {current} of {total_vids}...'
        })

        try:
            # Check duration before download (retries with cookies on age restriction)
            info = _extract_with_cookie_fallback(v_url, params, download=False)
            duration = info.get('duration', 0)
            if duration > settings.MAX_VIDEO_DURATION:
                logger.warning(f"Skipping video {v_url} because it exceeds duration limit.")
                continue
            # Download with same fallback logic (cookies + CLI for age-restricted YouTube)
            _extract_with_cookie_fallback(v_url, params, download=True)
        except Exception as e:
            logger.error(f"Failed to download {v_url}: {e}")

    self.update_state(state='PROGRESS', meta={
        'percent': 95,
        'status': 'Zipping files...'
    })
    
    zip_path = os.path.join(output_dir, f'Playlist_{task_id}.zip')
    zip_folder_task(pl_temp_dir, zip_path)
    
    # Cleanup temp dir
    shutil.rmtree(pl_temp_dir, ignore_errors=True)
    
    return {
        'status': 'Finished',
        'file_path': zip_path,
        'title': f'Playlist_{task_id}',
        'ext': 'zip'
    }

@shared_task(bind=True)
def download_video_task(self, url, type='video', itag=0, typeitag='', quality='best', output_dir='content-downloads'):
    
    os.makedirs(output_dir, exist_ok=True)
    
    video_id = 'dl_' + self.request.id
    reddit_best = 'reddit.com' in url and type == 'video' and quality == 'best' and not itag

    if reddit_best:
        dl_format = 'bestvideo+bestaudio/best'
        filetype = 'mp4'
    else:
        match type:
            case 'video':
                dl_format = get_format_for_quality('video', quality)
                filetype  = 'mp4'
            case 'audio':
                dl_format = get_format_for_quality('audio', quality)
                filetype  = 'mp3'
            case 'transcript' | 'subtitle':
                dl_format = None
                filetype  = 'srt'
            case _:
                dl_format = 'bestaudio'
                filetype  = 'mp3'

    if itag and type not in ('transcript', 'subtitle'):
        dl_format = itag
    if typeitag:
        filetype = typeitag

    final_path = os.path.join(output_dir, f'{video_id}.{filetype}')
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            
            # Fallback to parsing _percent_str if bytes are missing
            if not total:
                p = d.get('_percent_str', '0%').replace('%', '').strip()
                # Clean ANSI escape sequences if any
                import re
                p = re.sub(r'\x1b\[[0-9;]*m', '', p)
                try:
                    percent = float(p)
                except:
                    percent = 0
            else:
                percent = (downloaded / total) * 100

            filename = os.path.basename(d.get('filename', 'file'))
            self.update_state(state='PROGRESS', meta={
                'percent': percent,
                'status': f'Downloading {filename}...'
            })
        elif d['status'] == 'finished':
            self.update_state(state='PROGRESS', meta={
                'percent': 100,
                'status': 'Download finished, preparing for next step...'
            })

    def postprocessor_hook(d):
        pp_name = d.get('postprocessor')
        if d['status'] == 'started':
            self.update_state(state='PROGRESS', meta={
                'percent': 100,
                'status': f'Processing: {pp_name}...'
            })
        elif d['status'] == 'finished':
            self.update_state(state='PROGRESS', meta={
                'percent': 100,
                'status': f'Finished processing {pp_name}.'
            })

    # Pre-check duration
    title = video_id
    try:
        info = _extract_with_cookie_fallback(url, {'quiet': True}, download=False)
        duration = info.get('duration', 0)
        if duration > settings.MAX_VIDEO_DURATION:
            raise ValueError(f"Video is too long ({round(duration/3600, 1)}h). Max limit is {int(settings.MAX_VIDEO_DURATION/3600)}h.")
        title = info.get('title') or video_id
    except Exception as e:
        if "Video is too long" in str(e): raise
        # If it's another error, we'll let the main download try and catch it (it might be a playlist/unsupported URL)

    if reddit_best:
        final_path = _download_reddit_best_video(url, output_dir, video_id)
        return {
            'status': 'Finished',
            'file_path': final_path,
            'title': title,
            'ext': 'mp4'
        }

    ydl_opts = {
        'quiet':               True,
        'format':              dl_format,
        'outtmpl':             final_path,
        'writethumbnail':      True,
        'progress_hooks':      [progress_hook],
        'postprocessor_hooks': [postprocessor_hook],
        'postprocessors': [
            {'key': 'EmbedThumbnail'},
            {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg', 'when': 'before_dl'},
            {'key': 'FFmpegMetadata', 'add_metadata': True},
        ],
        'merge_output_format': 'mp4' if (type == 'video' and not itag) else None,
    }

    if type == 'audio':
        ydl_opts['postprocessors'].insert(0, {
            'key':              'FFmpegExtractAudio',
            'preferredcodec':   'mp3',
            'preferredquality': '192',
        })

    try:
        info = _extract_with_cookie_fallback(url, ydl_opts, download=True)

        # When CLI handles the download, info is None.
        # Fallback to the outtmpl path and pre-fetched title.
        if info:
            try:
                final_path = info['requested_downloads'][0]['filepath']
            except Exception:
                pass
            title = info.get('title') or title

        ext = final_path.split('.')[-1]
        
        return {
            'status': 'Finished',
            'file_path': final_path,
            'title': title,
            'ext': ext
        }
    except Exception as e:
        logger.exception("Download task failed")
        raise

@shared_task
def update_ytdlp_task():
    try:
        subprocess.run(["pip", "install", "-U", "yt-dlp"], check=True)
        logger.info("yt-dlp updated successfully.")
    except Exception as e:
        logger.error(f"Failed to update yt-dlp: {e}")


# ---------------------------------------------------------------------------
# Analytics / GeoIP helpers
# ---------------------------------------------------------------------------
import os
from .models import RequestLog, DailySummary

try:
    import geoip2.database

    GEOIP2_AVAILABLE = True
except ImportError:  # pragma: no cover
    GEOIP2_AVAILABLE = False


def _get_geoip_db_path():
    return getattr(
        settings, "GEOIP_PATH", os.path.join(settings.BASE_DIR, "GeoLite2-City.mmdb")
    )


@shared_task
def geolocate_request_task(log_id, ip):
    """Enrich a RequestLog row with GeoIP data using a local MaxMind database."""
    if not GEOIP2_AVAILABLE:
        return

    db_path = _get_geoip_db_path()
    if not os.path.exists(db_path):
        logger.warning(
            "GeoIP database not found at %s. Skipping enrichment.", db_path
        )
        return

    try:
        with geoip2.database.Reader(db_path) as reader:
            response = reader.city(ip)
            RequestLog.objects.filter(id=log_id).update(
                country_code=(response.country.iso_code or ""),
                country_name=(response.country.name or ""),
                city=(response.city.name or ""),
                latitude=response.location.latitude,
                longitude=response.location.longitude,
            )
    except Exception:
        # Silently ignore private/range IP lookups
        pass


@shared_task
def aggregate_daily_summary_task():
    """Aggregate yesterday's stats into DailySummary."""
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Count

    today = timezone.now().date()
    yesterday = today - timedelta(days=1)

    logs = RequestLog.objects.filter(timestamp__date=yesterday)
    total = logs.count()
    downloads = logs.filter(is_download=True).count()
    unique = logs.values("ip_address").distinct().count()

    ds, _ = DailySummary.objects.update_or_create(
        date=yesterday,
        defaults={
            "total_requests": total,
            "unique_ips": unique,
            "total_downloads": downloads,
        },
    )
    return str(ds)
