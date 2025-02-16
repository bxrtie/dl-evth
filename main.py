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

def get_yt_dlp_opts(format_info: dict, output_template: str, download_id: str, video_source: str) -> dict:
    """Get yt-dlp options based on format and video source."""
    common_opts = {
        'progress_hooks': [create_progress_hook(download_id)],
        'outtmpl': output_template
    }
    
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
            # For TikTok and Instagram, we want to download the best quality available
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
async def download_video(request: VideoRequest):
    try:
        # Generate unique ID for progress tracking
        download_id = str(uuid.uuid4())
        
        # Determine video source
        video_source = get_video_source(request.url)
        
        # First, get video information
        with yt_dlp.YoutubeDL() as ydl:
            info = ydl.extract_info(request.url, download=False)
            video_title = info.get('title', 'video')
            
        # Create safe filename from video title
        safe_title = sanitize_filename(video_title)

        # Get format configuration
        format_info = get_format_info(request.format)
        if not format_info:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {request.format}")

        # Initialize progress
        download_progress[download_id] = {'status': 'starting'}

        # Set up output template and final filename
        if format_info['type'] == 'audio':
            final_filename = f"{safe_title}.{request.format}"
        else:
            final_filename = f"{safe_title}.{format_info['config']['ext']}"
        
        output_template = str(DOWNLOAD_DIR / '%(title)s.%(ext)s')
        
        # Get yt-dlp options
        ydl_opts = get_yt_dlp_opts(format_info, output_template, download_id, video_source)

        # Start download process
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                error_code = ydl.download([request.url])
                if error_code != 0:
                    raise Exception("Download failed")
        except Exception as e:
            download_progress[download_id] = {'status': 'error', 'error': str(e)}
            raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")

        # Update progress to finished
        download_progress[download_id] = {'status': 'finished', 'filename': final_filename}
        
        return {
            "status": "success", 
            "filename": final_filename, 
            "download_id": download_id
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Clean up progress data in case of error
        if download_id in download_progress:
            download_progress[download_id] = {'status': 'error', 'error': str(e)}
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/file-info")
async def get_file_info(request: FileInfoRequest):
    try:
        # Validate video source
        video_source = get_video_source(request.url)
        
        format_info = get_format_info(request.format)
        if not format_info:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {request.format}")

        # Configure yt-dlp options based on video source
        if video_source in ['tiktok', 'instagram']:
            ydl_opts = {'format': 'best'}
        else:  # youtube
            ydl_opts = {
                'format': format_info['config']['format'] if format_info['type'] == 'video' else 'bestaudio/best',
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            # Get file size based on format and source
            if format_info['type'] == 'video':
                if video_source in ['tiktok', 'instagram']:
                    # For TikTok and Instagram, use the best format's filesize
                    filesize = info.get('filesize') or info.get('filesize_approx')
                else:
                    # For YouTube, handle multiple formats
                    formats = info['formats']
                    selected_format = None
                    for f in formats:
                        if f.get('ext') == format_info['config']['ext']:
                            if not selected_format or (f.get('filesize') or 0) > (selected_format.get('filesize') or 0):
                                selected_format = f
                    filesize = selected_format.get('filesize') if selected_format else info.get('filesize')
            else:
                # For audio, get the best audio format size
                formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                filesize = max((f.get('filesize') or 0) for f in formats)

            # Estimate download speed (assume 5MB/s as average)
            avg_speed = 5 * 1024 * 1024  # 5MB/s in bytes/s
            estimated_time = filesize / avg_speed if filesize else None

            return {
                "title": info.get('title'),
                "duration": info.get('duration'),
                "filesize": filesize,
                "estimated_time": estimated_time,
                "avg_speed": avg_speed
            }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
