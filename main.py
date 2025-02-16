from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import os
import uuid
from pathlib import Path
import json
from typing import Dict
import re
from urllib.parse import urlparse
import time
import asyncio
import random
import requests
from bs4 import BeautifulSoup
from typing import Optional, List, Dict
import subprocess

app = FastAPI()

# Create directories if they don't exist
STATIC_DIR = Path("static")
DOWNLOAD_DIR = Path("downloads")

# FFmpeg is pre-installed on Render
FFMPEG_DIR = None  # Let yt-dlp find ffmpeg automatically

STATIC_DIR.mkdir(exist_ok=True)
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Store progress information in memory
download_progress: Dict[str, dict] = {}

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

# Configure CORS with specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, you should specify your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str
    format: str

class FileInfoRequest(BaseModel):
    url: str
    format: str

def get_video_source(url: str) -> str:
    """Determine if the URL is from YouTube, TikTok, or Instagram."""
    domain = urlparse(url).netloc.lower()
    if any(x in domain for x in ['youtube.com', 'youtu.be']):
        return 'youtube'
    elif any(x in domain for x in ['tiktok.com']):
        return 'tiktok'
    elif any(x in domain for x in ['instagram.com']):
        return 'instagram'
    else:
        raise ValueError("Unsupported video platform. Only YouTube, TikTok, and Instagram are supported.")

def sanitize_filename(title: str) -> str:
    # Remove invalid characters but preserve spaces
    sanitized = re.sub(r'[<>:"/\\|?*]', '', title)  # Remove invalid Windows filename chars
    return sanitized  # Keep original spaces and characters

def get_format_info(format: str):
    audio_formats = {
        'mp3': {'preferredcodec': 'mp3', 'preferredquality': '192'},
        'wav': {'preferredcodec': 'wav', 'preferredquality': '192'},
        'flac': {'preferredcodec': 'flac', 'preferredquality': '192'}
    }

    video_formats = {
        'mp4': {'ext': 'mp4', 'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'},
        'webm': {'ext': 'webm', 'format': 'bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'},
        'mov': {'ext': 'mov', 'format': 'best[ext=mov]/best'},
        'avi': {'ext': 'avi', 'format': 'best[ext=avi]/best'},
        'mkv': {'ext': 'mkv', 'format': 'bestvideo+bestaudio/best'},
        'mpeg': {'ext': 'mpeg', 'format': 'best[ext=mpeg]/best'}
    }

    if format in audio_formats:
        return {'type': 'audio', 'config': audio_formats[format]}
    elif format in video_formats:
        return {'type': 'video', 'config': video_formats[format]}
    else:
        return None

def get_proxies() -> List[Dict[str, str]]:
    """Get a list of free proxies."""
    try:
        response = requests.get('https://free-proxy-list.net/')
        soup = BeautifulSoup(response.text, 'html.parser')
        
        proxies = []
        table = soup.find('table')
        if table:
            rows = table.find_all('tr')[1:]  # Skip header row
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 7 and cols[6].text.strip() == 'yes':  # Only HTTPS proxies
                    proxy = {
                        'http': f'http://{cols[0].text.strip()}:{cols[1].text.strip()}',
                        'https': f'https://{cols[0].text.strip()}:{cols[1].text.strip()}'
                    }
                    proxies.append(proxy)
        return proxies
    except Exception:
        return []

def get_random_proxy() -> Optional[Dict[str, str]]:
    """Get a random proxy from the list."""
    proxies = get_proxies()
    return random.choice(proxies) if proxies else None

def get_yt_dlp_opts(format_info: dict, output_template: str, download_id: str, video_source: str) -> dict:
    """Get yt-dlp options based on format and video source."""
    proxy = get_random_proxy()
    
    common_opts = {
        'progress_hooks': [create_progress_hook(download_id)],
        'outtmpl': output_template,
        'no_warnings': False,
        'quiet': False,
        'verbose': True,
        'socket_timeout': 30,
        'retries': 10,
        'file_access_retries': 10,
        'fragment_retries': 10,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.youtube.com/',
            'Sec-Ch-Ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        }
    }
    
    if proxy:
        common_opts['proxy'] = proxy.get('http')
    
    # Only add ffmpeg_location if it exists
    if FFMPEG_DIR:
        common_opts['ffmpeg_location'] = str(FFMPEG_DIR)
    
    if format_info['type'] == 'audio':
        opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': format_info['config']['preferredcodec'],
                'preferredquality': format_info['config']['preferredquality'],
            }],
        }
    else:  # video
        if video_source in ['tiktok', 'instagram']:
            opts = {
                'format': 'best',
                'merge_output_format': format_info['config']['ext']
            }
        else:  # youtube
            opts = {
                'format': format_info['config']['format'],
                'merge_output_format': format_info['config']['ext']
            }
    
    return {**common_opts, **opts}

class InnertubeAPI:
    def __init__(self):
        self.base_url = "https://www.youtube.com/youtubei/v1"
        self.client = {
            'clientName': 'ANDROID',
            'clientVersion': '17.31.35',
            'androidSdkVersion': 30,
            'hl': 'en',
            'gl': 'US',
            'platform': 'MOBILE'
        }
        self.key = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # Public Android client key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'com.google.android.youtube/17.31.35 (Linux; U; Android 11) gzip',
            'Accept': '*/*',
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': self.key,
            'Host': 'www.youtube.com',
            'Connection': 'Keep-Alive',
            'Accept-Encoding': 'gzip'
        })

    def get_video_info(self, video_id: str) -> dict:
        url = f"{self.base_url}/player?key={self.key}"
        data = {
            'videoId': video_id,
            'context': {
                'client': self.client,
                'thirdParty': {
                    'embedUrl': 'https://www.youtube.com'
                }
            },
            'playbackContext': {
                'contentPlaybackContext': {
                    'html5Preference': 'HTML5_PREF_WANTS'
                }
            }
        }
        
        response = self.session.post(url, json=data)
        response.raise_for_status()
        return response.json()

    def extract_video_formats(self, video_info: dict) -> list:
        formats = []
        try:
            for fmt in video_info.get('streamingData', {}).get('adaptiveFormats', []):
                formats.append({
                    'url': fmt.get('url'),
                    'mimeType': fmt.get('mimeType', ''),
                    'quality': fmt.get('quality', ''),
                    'bitrate': fmt.get('bitrate', 0),
                    'width': fmt.get('width', 0),
                    'height': fmt.get('height', 0),
                    'contentLength': fmt.get('contentLength', '0'),
                    'type': 'video' if fmt.get('mimeType', '').startswith('video') else 'audio'
                })
            return formats
        except Exception as e:
            print(f"Error extracting formats: {e}")
            return []

def get_video_id(url: str) -> str:
    """Extract video ID from YouTube URL."""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/|v\/|youtu.be\/)([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError("Invalid YouTube URL")

def download_with_innertube(url: str, format_info: dict, output_path: str) -> dict:
    """Download video using Innertube API."""
    try:
        video_id = get_video_id(url)
        api = InnertubeAPI()
        video_info = api.get_video_info(video_id)
        formats = api.extract_video_formats(video_info)
        
        if not formats:
            raise ValueError("No formats found")
            
        # Select best format based on requirements
        if format_info['type'] == 'video':
            video_formats = [f for f in formats if f['type'] == 'video' and 'mp4' in f['mimeType'].lower()]
            audio_formats = [f for f in formats if f['type'] == 'audio' and 'm4a' in f['mimeType'].lower()]
            
            if not video_formats or not audio_formats:
                raise ValueError("Required formats not found")
                
            # Get best video and audio formats
            video_format = max(video_formats, key=lambda x: x['bitrate'])
            audio_format = max(audio_formats, key=lambda x: x['bitrate'])
            
            # Download video and audio separately
            video_path = f"{output_path}.video.mp4"
            audio_path = f"{output_path}.audio.m4a"
            
            # Download video
            video_response = requests.get(video_format['url'], stream=True)
            video_response.raise_for_status()
            with open(video_path, 'wb') as f:
                for chunk in video_response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            # Download audio
            audio_response = requests.get(audio_format['url'], stream=True)
            audio_response.raise_for_status()
            with open(audio_path, 'wb') as f:
                for chunk in audio_response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            # Merge video and audio
            if FFMPEG_DIR:
                ffmpeg_path = str(FFMPEG_DIR / 'ffmpeg')
            else:
                ffmpeg_path = 'ffmpeg'
                
            subprocess.run([
                ffmpeg_path,
                '-i', video_path,
                '-i', audio_path,
                '-c', 'copy',
                output_path
            ], check=True)
            
            # Clean up temporary files
            os.remove(video_path)
            os.remove(audio_path)
            
        else:  # audio only
            audio_formats = [f for f in formats if f['type'] == 'audio']
            if not audio_formats:
                raise ValueError("No audio formats found")
                
            audio_format = max(audio_formats, key=lambda x: x['bitrate'])
            response = requests.get(audio_format['url'], stream=True)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        return {
            'success': True,
            'message': 'Download completed successfully'
        }
        
    except Exception as e:
        raise Exception(f"Download failed: {str(e)}")

@app.get("/")
async def read_root():
    return FileResponse(str(STATIC_DIR / "index.html"))

def create_progress_hook(download_id: str):
    def progress_hook(d):
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes')
            downloaded_bytes = d.get('downloaded_bytes', 0)
            
            # Calculate percentage only if total_bytes is available
            percent = (downloaded_bytes / total_bytes * 100) if total_bytes else 0
            
            download_progress[download_id] = {
                'status': 'downloading',
                'downloaded_bytes': downloaded_bytes,
                'total_bytes': total_bytes,
                'speed': d.get('speed', 0),
                'eta': d.get('eta', 0),
                'filename': d.get('filename', ''),
                'percent': percent
            }
        elif d['status'] == 'finished':
            download_progress[download_id] = {
                'status': 'finished',
                'filename': d.get('filename', '')
            }
        else:
            download_progress[download_id] = {
                'status': d['status']
            }
    return progress_hook

@app.post("/download")
async def download_file(request: VideoRequest):
    try:
        # Validate video source
        video_source = get_video_source(request.url)
        
        format_info = get_format_info(request.format)
        if not format_info:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {request.format}")
        
        # Create unique download ID and output path
        download_id = str(uuid.uuid4())
        download_progress[download_id] = {'status': 'downloading', 'progress': 0}
        
        output_filename = sanitize_filename(f"{download_id}.{format_info['config']['ext']}")
        output_path = DOWNLOAD_DIR / output_filename
        
        if video_source == 'youtube':
            result = download_with_innertube(request.url, format_info, str(output_path))
            download_progress[download_id] = {'status': 'completed', 'filename': output_filename}
        else:
            # Use yt-dlp for other sources
            ydl_opts = get_yt_dlp_opts(format_info, str(output_path), download_id, video_source)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([request.url])
        
        return {"download_id": download_id, "filename": output_filename}
        
    except Exception as e:
        if download_id in download_progress:
            download_progress[download_id]['status'] = 'error'
            download_progress[download_id]['error'] = str(e)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/file-info")
async def get_file_info(request: FileInfoRequest):
    try:
        # Validate video source
        video_source = get_video_source(request.url)
        
        format_info = get_format_info(request.format)
        if not format_info:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {request.format}")

        # Configure yt-dlp options
        ydl_opts = {
            'quiet': False,  # Enable output for debugging
            'no_warnings': False,  # Enable warnings for debugging
            'verbose': True,  # Add verbose output
            'socket_timeout': 30,
            'retries': 10,
            'file_access_retries': 10,
            'fragment_retries': 10,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.youtube.com/',
                'Sec-Ch-Ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
            },
            'extractor_args': {
                'youtube': {
                    'player_skip': ['js', 'configs', 'webpage']
                }
            }
        }
        
        if video_source in ['tiktok', 'instagram']:
            ydl_opts['format'] = 'best'
        else:  # youtube
            ydl_opts['format'] = format_info['config']['format'] if format_info['type'] == 'video' else 'bestaudio/best'

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(request.url, download=False)
                if not info:
                    raise HTTPException(status_code=400, detail="Could not extract video information")
                
                return {
                    "title": info.get('title', 'Unknown Title'),
                    "duration": info.get('duration', 0),
                    "thumbnail": info.get('thumbnail', ''),
                    "format": request.format
                }
            except yt_dlp.utils.DownloadError as e:
                error_message = str(e)
                if "Sign in to confirm you're not a bot" in error_message:
                    raise HTTPException(
                        status_code=400,
                        detail="YouTube is requesting verification. Please try again in a few minutes or try a different video."
                    )
                raise HTTPException(status_code=400, detail=f"Download error: {error_message}")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Unexpected error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/progress/{download_id}")
async def get_progress(download_id: str):
    if download_id in download_progress:
        return download_progress[download_id]
    return {"status": "not_found"}

@app.get("/download/{filename}")
async def get_file(filename: str):
    try:
        file_path = DOWNLOAD_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type='application/octet-stream'
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Clean up downloads periodically
def cleanup_downloads():
    """Delete files older than 1 hour"""
    try:
        current_time = time.time()
        for file_path in DOWNLOAD_DIR.glob("*"):
            if file_path.is_file():
                if current_time - file_path.stat().st_mtime > 3600:  # 1 hour
                    try:
                        file_path.unlink()
                    except Exception as e:
                        print(f"Error deleting file {file_path}: {e}")
    except Exception as e:
        print(f"Error during cleanup: {e}")

# Schedule cleanup every hour
@app.on_event("startup")
async def startup_event():
    print("Starting application...")
    print(f"Static directory: {STATIC_DIR}")
    print(f"Download directory: {DOWNLOAD_DIR}")
    cleanup_downloads()  # Initial cleanup
    
    # Start background cleanup task
    asyncio.create_task(periodic_cleanup())

async def periodic_cleanup():
    while True:
        await asyncio.sleep(3600)  # Run every hour
        cleanup_downloads()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting server on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
