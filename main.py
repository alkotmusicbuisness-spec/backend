
import os, re, shutil, tempfile, zipfile
from pathlib import Path
from urllib.parse import urlparse
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="StreamGrab API", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
)

YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {"player_client": ["mweb"]},
    "youtubepot-bgutilhttp": {
        "base_url": [os.getenv("POT_PROVIDER_URL", "http://127.0.0.1:4416")]
    },
}

def valid_url(url):
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)

def ydl_opts(**extra):
    return {
        "quiet": True, "no_warnings": True,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        "js_runtimes": {"deno": {}},
        "retries": 3, "socket_timeout": 30,
        **extra
    }

def extract(url, **extra):
    if not valid_url(url):
        raise HTTPException(400, "Invalid URL")
    try:
        with yt_dlp.YoutubeDL(ydl_opts(**extra)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        msg = str(e)
        if "Sign in to confirm" in msg or "not a bot" in msg:
            raise HTTPException(
                502,
                "YouTube rejected the Railway server as automated traffic. "
                "The JS challenge solver and PO-token provider are enabled, "
                "but YouTube can still reject a flagged server IP."
            )
        raise HTTPException(400, f"YouTube extraction failed: {msg}")

def video_formats(info):
    result, heights = [], set()
    for f in info.get("formats", []):
        fid, v, h = f.get("format_id"), f.get("vcodec"), f.get("height")
        if not fid or not v or v == "none" or not h or h in heights:
            continue
        a = f.get("acodec")
        result.append({
            "format_id": fid, "height": h,
            "label": f"{h}p" + (f" {f.get('fps'):g}fps" if f.get("fps") else "") + f" â€¢ {f.get('ext','?')}",
            "has_audio": bool(a and a != "none")
        })
        heights.add(h)
    return sorted(result, key=lambda x: (x["height"], x["has_audio"]), reverse=True)

def audio_formats(info):
    result, seen = [], set()
    for f in info.get("formats", []):
        fid, a = f.get("format_id"), f.get("acodec")
        if not fid or not a or a == "none" or fid in seen:
            continue
        abr = f.get("abr") or 0
        result.append({"format_id": fid, "abr": abr, "label": f"{round(abr)} kbps â€¢ {f.get('ext','?')}"})
        seen.add(fid)
    return sorted(result, key=lambda x: x["abr"], reverse=True)

@app.get("/")
def root():
    return {"status": "ok", "service": "StreamGrab API", "version": "2.1.0"}

@app.get("/api/analyze")
def analyze(url: str):
    info = extract(url, noplaylist=False)
    if info.get("_type") == "playlist" or info.get("entries") is not None:
        entries = []
        for e in info.get("entries") or []:
            if e:
                entries.append({
                    "id": e.get("id"), "title": e.get("title") or "Untitled",
                    "url": e.get("webpage_url") or e.get("url"),
                    "thumbnail": e.get("thumbnail"), "max_height": e.get("height")
                })
        return {
            "kind": "playlist", "title": info.get("title") or "Playlist",
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url") or url,
            "entry_count": len(entries), "entries": entries
        }
    return {
        "kind": "video", "title": info.get("title", "Video"),
        "thumbnail": info.get("thumbnail"), "duration": info.get("duration"),
        "uploader": info.get("uploader"), "webpage_url": info.get("webpage_url", url),
        "video_formats": video_formats(info), "audio_formats": audio_formats(info)
    }

@app.get("/api/formats")
def formats(url: str):
    return analyze(url)

@app.get("/api/download")
def download(url: str, format_id: str, type: str = "mp4"):
    if type not in ("mp4", "mp3"):
        raise HTTPException(400, "type must be mp4 or mp3")
    info = extract(url, noplaylist=True)
    safe = re.sub(r"[^A-Za-z0-9._ -]", "", info.get("title", "download"))[:100].strip() or "download"
    temp = Path(tempfile.mkdtemp(prefix="streamgrab-"))
    out = temp / (safe + ".%(ext)s")
    opts = ydl_opts(
        noplaylist=True, format=format_id if type == "mp3" else f"{format_id}+bestaudio/best",
        outtmpl=str(out), merge_output_format="mp4" if type == "mp4" else None,
        postprocessors=[] if type == "mp4" else [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"
        }]
    )
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
    return FileResponse(
        file, media_type="audio/mpeg" if file.suffix.lower() == ".mp3" else "video/mp4",
        filename=file.name, background=BackgroundTask(shutil.rmtree, temp, ignore_errors=True)
    )

@app.get("/api/playlist/download")
def playlist_download(url: str, type: str = "mp4", quality: str = "best"):
    if type not in ("mp4", "mp3"):
        raise HTTPException(400, "type must be mp4 or mp3")
    temp = Path(tempfile.mkdtemp(prefix="streamgrab-playlist-"))
    media = temp / "files"; media.mkdir()
    archive = temp / "playlist.zip"
    try:
        if type == "mp4":
            try: q = int(quality)
            except ValueError: q = 99999
            fmt = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
            post = []
        else:
            fmt = "bestaudio/best"
            post = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(quality)}]
        opts = ydl_opts(
            noplaylist=False, ignoreerrors=True, format=fmt,
            outtmpl=str(media / "%(playlist_index)03d - %(title).180s.%(ext)s"),
            postprocessors=post, merge_output_format="mp4" if type == "mp4" else None
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = [p for p in media.rglob("*") if p.is_file()]
        if not files: raise RuntimeError("No playlist files were created")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(files): z.write(f, arcname=f.name)
        title = (info or {}).get("title", "playlist")
        name = re.sub(r"[^A-Za-z0-9._ -]", "", title)[:80].strip() or "playlist"
        return FileResponse(archive, media_type="application/zip", filename=name+".zip",
                            background=BackgroundTask(shutil.rmtree, temp, ignore_errors=True))
    except Exception as e:
        shutil.rmtree(temp, ignore_errors=True)
        raise HTTPException(500, f"Playlist download failed: {e}")
