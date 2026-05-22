import os
import yt_dlp
import logging
import zipfile
import shutil
import subprocess
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

# Constants are now in settings.py

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
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        try:
            # Use the actual filepath from the info dict if available
            if 'requested_downloads' in info and info['requested_downloads']:
                return info['requested_downloads'][0]['filepath']
            return ydl.prepare_filename(info)
        except Exception:
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
            with yt_dlp.YoutubeDL(params) as ydl:
                # Check duration before download
                info = ydl.extract_info(v_url, download=False)
                duration = info.get('duration', 0)
                if duration > settings.MAX_VIDEO_DURATION:
                    logger.warning(f"Skipping video {v_url} because it exceeds duration limit.")
                    continue
                ydl.download([v_url])
        except Exception as e:
            logger.error(f"Failed to download {v_url}: {e}")
            # Continue with others even if one fails? 
            # For now, let's just log it.

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
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
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
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Update final path if changed by post-processors
            try:
                final_path = info['requested_downloads'][0]['filepath']
            except:
                pass
            
            title = info.get('title') or 'download'
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
