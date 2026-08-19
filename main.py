from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from curl_cffi.requests import AsyncSession
import os
import uuid

app = FastAPI()

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

class VideoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    format_id: str

def remove_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"Error removing file {path}: {e}")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/get-qualities")
async def get_qualities(req: VideoRequest):
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'impersonate': ImpersonateTarget.from_str('chrome'),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            
        title = info.get('title', 'Unknown Title')
        formats = info.get('formats', [])
        
        # Filter formats to those that have both video and audio (to avoid needing ffmpeg to merge on Render)
        available_formats = []
        for f in formats:
            if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                res = f.get('resolution') or f"{f.get('width', '?')}x{f.get('height', '?')}"
                ext = f.get('ext', 'mp4')
                format_id = f.get('format_id')
                note = f.get('format_note', '')
                
                # Prevent duplicates (sometimes yt-dlp returns multiple identical formats)
                if not any(x['format_id'] == format_id for x in available_formats):
                    available_formats.append({
                        "format_id": format_id,
                        "resolution": res,
                        "ext": ext,
                        "note": note
                    })
        
        # If no combined formats found, fallback to anything with a video codec
        if not available_formats:
            for f in formats:
                if f.get('vcodec') != 'none':
                    res = f.get('resolution') or f"{f.get('width', '?')}x{f.get('height', '?')}"
                    ext = f.get('ext', 'mp4')
                    format_id = f.get('format_id')
                    note = f.get('format_note', '')
                    
                    if not any(x['format_id'] == format_id for x in available_formats):
                        available_formats.append({
                            "format_id": format_id,
                            "resolution": res,
                            "ext": ext,
                            "note": note
                        })
                        
        # Sort by height if available
        # But we'll just return as is for simplicity, yt-dlp usually sorts worst to best.
        # Let's reverse it so best is first.
        available_formats.reverse()
        
        return {"title": title, "formats": available_formats}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/download")
async def download_video(url: str, format_id: str):
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'impersonate': ImpersonateTarget.from_str('chrome'),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info but DO NOT download yet
            info = ydl.extract_info(url, download=False)
            
        # Find the requested format
        target_format = next((f for f in info.get('formats', []) if f.get('format_id') == format_id), None)
        if not target_format:
            raise HTTPException(status_code=400, detail="Requested format not found.")
            
        video_url = target_format.get('url')
        http_headers = target_format.get('http_headers', {})
        
        # Calculate file size if available for the download manager
        filesize = target_format.get('filesize') or target_format.get('filesize_approx')
        
        # Prepare safe filename
        client_filename = f"{info.get('title', 'video')}.{target_format.get('ext', 'mp4')}"
        client_filename = "".join([c for c in client_filename if c.isalpha() or c.isdigit() or c in (' ', '.', '-', '_')]).rstrip()
        
        # Streaming generator
        async def stream_generator():
            async with AsyncSession(impersonate="chrome") as session:
                # We stream the content from YouTube directly to the client's browser
                response = await session.get(video_url, headers=http_headers, stream=True)
                async for chunk in response.aiter_content(chunk_size=65536):
                    if chunk:
                        yield chunk

        # Build response headers
        headers = {
            'Content-Disposition': f'attachment; filename="{client_filename}"',
        }
        if filesize:
            headers['Content-Length'] = str(filesize)

        return StreamingResponse(
            stream_generator(),
            media_type='application/octet-stream',
            headers=headers
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
