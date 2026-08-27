import os, re, shutil, tempfile, zipfile
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
import yt_dlp

app = FastAPI(title="StreamGrab API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False, allow_methods=["*"], allow_headers=["*"]
)

def valid_url(url: str):
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)

def ydl_info(url, **extra):
    if not valid_url(url):
        raise HTTPException(400, "Invalid URL")
    opts = {"quiet": True, "no_warnings": True, **extra}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(400, f"Could not analyze URL: {e}")

def video_formats(info):
    result, seen = [], set()
    for f in info.get("formats", []):
        fid, v, h = f.get("format_id"), f.get("vcodec"), f.get("height")
        if not fid or not v or v == "none" or not h or fid in seen:
            continue
        a = f.get("acodec")
        result.append({
            "format_id": fid, "height": h,
            "label": f"{h}p" + (f" {f.get('fps'):g}fps" if f.get("fps") else "") +
                     f" • {f.get('ext','?')} • {v.split('.')[0]}",
            "has_audio": bool(a and a != "none")
        })
        seen.add(fid)
    result.sort(key=lambda x: (x["height"], x["has_audio"]), reverse=True)
    best = {}
    for x in result:
        best.setdefault(x["height"], x)
    return list(best.values())

def audio_formats(info):
    result, seen = [], set()
    for f in info.get("formats", []):
        fid, a = f.get("format_id"), f.get("acodec")
        if not fid or not a or a == "none" or fid in seen:
            continue
        abr = f.get("abr") or 0
        result.append({"format_id": fid, "abr": abr, "label": f"{round(abr)} kbps • {f.get('ext','?')}"})
        seen.add(fid)
    result.sort(key=lambda x: x["abr"], reverse=True)
    return result

@app.get("/")
def root():
    return {"status": "ok", "service": "StreamGrab API", "version": "2.0.0"}

@app.get("/api/analyze")
def analyze(url: str):
    info = ydl_info(url, skip_download=True, noplaylist=False)
    if info.get("_type") == "playlist" or info.get("entries") is not None:
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            # Flat playlist extraction gives limited metadata; max_height is filled when available.
            entries.append({
                "id": e.get("id"),
                "title": e.get("title") or "Untitled",
                "url": e.get("webpage_url") or e.get("url"),
                "thumbnail": e.get("thumbnail"),
                "max_height": e.get("height")
            })
        return {
            "kind": "playlist",
            "title": info.get("title") or "Playlist",
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url") or url,
            "entry_count": len(entries),
            "entries": entries
        }
    return {
        "kind": "video",
        "title": info.get("title", "Video"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "webpage_url": info.get("webpage_url", url),
        "video_formats": video_formats(info),
        "audio_formats": audio_formats(info)
    }

@app.get("/api/formats")
def formats(url: str):
    return analyze(url)

@app.get("/api/download")
def download(url: str, format_id: str, type: str = "mp4"):
    if type not in ("mp4", "mp3"):
        raise HTTPException(400, "type must be mp4 or mp3")
    info = ydl_info(url, skip_download=True, noplaylist=True)
    safe = re.sub(r"[^A-Za-z0-9._ -]", "", info.get("title", "download"))[:100].strip() or "download"
    temp = Path(tempfile.mkdtemp(prefix="streamgrab-"))
    out = temp / (safe + ".%(ext)s")
    if type == "mp3":
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "format": format_id, "outtmpl": str(out),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
        }
    else:
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "format": f"{format_id}+bestaudio/best",
            "merge_output_format": "mp4", "outtmpl": str(out)
        }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        shutil.rmtree(temp, ignore_errors=True)
        raise HTTPException(500, f"Download failed: {e}")
    files = [p for p in temp.glob("*") if p.is_file()]
    if not files:
        shutil.rmtree(temp, ignore_errors=True)
        raise HTTPException(500, "No output file was created")
    file = files[0]
    media = "audio/mpeg" if file.suffix.lower() == ".mp3" else "video/mp4"
    return FileResponse(
        file, media_type=media, filename=file.name,
        background=BackgroundTask(shutil.rmtree, temp, ignore_errors=True)
    )

@app.get("/api/playlist/download")
def playlist_download(url: str, type: str = "mp4", quality: str = "best"):
    if type not in ("mp4", "mp3"):
        raise HTTPException(400, "type must be mp4 or mp3")
    temp = Path(tempfile.mkdtemp(prefix="streamgrab-playlist-"))
    media_dir = temp / "files"
    media_dir.mkdir()
    archive = temp / "playlist.zip"
    try:
        # Download each item using the best available stream at or below requested video quality.
        # For MP3, quality is the target bitrate passed to FFmpeg.
        if type == "mp4":
            try:
                q = int(quality)
            except ValueError:
                q = 99999
            fmt = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
            post = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
        else:
            fmt = "bestaudio/best"
            post = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(quality)}]
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": False,
            "ignoreerrors": True, "format": fmt,
            "outtmpl": str(media_dir / "%(playlist_index)03d - %(title).180s.%(ext)s"),
            "postprocessors": post,
            "merge_output_format": "mp4" if type == "mp4" else None,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        files = [p for p in media_dir.rglob("*") if p.is_file()]
        if not files:
            raise RuntimeError("No playlist files were created")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for f in sorted(files):
                z.write(f, arcname=f.name)
        name = re.sub(r"[^A-Za-z0-9._ -]", "", Path(yt_dlp.YoutubeDL({"quiet": True}).extract_info(url, download=False).get("title", "playlist")).name)
        name = (name[:80].strip() or "playlist") + ".zip"
        return FileResponse(
            archive, media_type="application/zip", filename=name,
            background=BackgroundTask(shutil.rmtree, temp, ignore_errors=True)
        )
    except Exception as e:
        shutil.rmtree(temp, ignore_errors=True)
        raise HTTPException(500, f"Playlist download failed: {e}")
