import os
import yt_dlp
import logging
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def download_video_task(self, url, type='video', itag=0, typeitag='', output_dir='content-downloads'):
    
    os.makedirs(output_dir, exist_ok=True)
    
    video_id = 'dl_' + self.request.id
    
    match type:
        case 'video':
            dl_format = 'bestvideo+bestaudio/best'
            filetype  = 'mp4'
        case 'audio':
            dl_format = 'bestaudio'
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
