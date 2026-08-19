from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import yt_dlp
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

@app.post("/api/download")
async def download_video(req: DownloadRequest, background_tasks: BackgroundTasks):
    os.makedirs("downloads", exist_ok=True)
    file_id = str(uuid.uuid4())
    outtmpl = f"downloads/{file_id}.%(ext)s"
    
    ydl_opts = {
        'format': req.format_id,
        'outtmpl': outtmpl,
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            filename = ydl.prepare_filename(info)
            
            # If standard output template doesn't exactly match prepare_filename (can happen sometimes)
            if not os.path.exists(filename):
                filename = f"downloads/{file_id}.{info.get('ext', 'mp4')}"
                
        # Schedule cleanup
        background_tasks.add_task(remove_file, filename)
        
        client_filename = f"{info.get('title', 'video')}.{info.get('ext', 'mp4')}"
        # Make filename safe
        client_filename = "".join([c for c in client_filename if c.isalpha() or c.isdigit() or c in (' ', '.', '-', '_')]).rstrip()
        
        return FileResponse(
            path=filename,
            filename=client_filename,
            media_type='application/octet-stream'
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
