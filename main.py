import asyncio, json, os, re, tempfile
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import yt_dlp

app=FastAPI(title="StreamGrab API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS","*").split(",")], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def valid_url(url:str):
    p=urlparse(url)
    return p.scheme in ("http","https") and p.netloc

def extract(url):
    if not valid_url(url): raise HTTPException(400,"Invalid URL")
    opts={"quiet":True,"no_warnings":True,"skip_download":True,"noplaylist":True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url,download=False)
    except Exception as e: raise HTTPException(400,f"Could not analyze video: {e}")

def label_video(f):
    h=f.get("height"); fps=f.get("fps"); ext=f.get("ext","?"); codec=f.get("vcodec","?")
    return f"{h}p" + (f" {fps:g}fps" if fps else "") + f" • {ext} • {codec.split('.')[0]}"

@app.get("/")
def root(): return {"status":"ok","service":"StreamGrab API"}

@app.get("/api/formats")
def formats(url:str):
    info=extract(url); video=[]; audio=[]; seen=set()
    for f in info.get("formats",[]):
        fid=f.get("format_id")
        if not fid or fid in seen: continue
        v=f.get("vcodec"); a=f.get("acodec")
        if v and v!="none" and f.get("height"):
            # Prefer formats that already include audio; otherwise yt-dlp will merge with best audio.
            video.append({"format_id":fid,"height":f.get("height"),"label":label_video(f),"has_audio":bool(a and a!="none")})
            seen.add(fid)
        elif a and a!="none":
            abr=f.get("abr") or 0
            audio.append({"format_id":fid,"abr":abr,"label":f"{round(abr)} kbps • {f.get('ext','?')}"})
            seen.add(fid)
    video.sort(key=lambda x:(x["height"],x["has_audio"]),reverse=True)
    audio.sort(key=lambda x:x["abr"],reverse=True)
    # De-duplicate visible resolutions, keeping the best candidate for each resolution.
    best={}
    for x in video: best.setdefault(x["height"],x)
    video=list(best.values())
    return {"title":info.get("title","Video"),"thumbnail":info.get("thumbnail"),"duration":info.get("duration"),"uploader":info.get("uploader"),"webpage_url":info.get("webpage_url",url),"video_formats":video,"audio_formats":audio}

@app.get("/api/download")
def download(url:str,format_id:str,type:str="mp4"):
    if type not in ("mp4","mp3"): raise HTTPException(400,"type must be mp4 or mp3")
    info=extract(url); safe=re.sub(r"[^A-Za-z0-9._ -]","",info.get("title","download"))[:100].strip() or "download"
    temp=tempfile.mkdtemp(prefix="streamgrab-"); out=Path(temp)/(safe+".%(ext)s")
    if type=="mp3":
        opts={"quiet":True,"no_warnings":True,"noplaylist":True,"format":format_id,"outtmpl":str(out),"postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"0"}]}
    else:
        opts={"quiet":True,"no_warnings":True,"noplaylist":True,"format":f"{format_id}+bestaudio/best","merge_output_format":"mp4","outtmpl":str(out)}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
    except Exception as e:
        raise HTTPException(500,f"Download failed: {e}")
    files=[p for p in Path(temp).glob("*") if p.is_file()]
    if not files: raise HTTPException(500,"No output file was created")
    file=files[0]
    media="audio/mpeg" if file.suffix==".mp3" else "video/mp4"
    return FileResponse(file,media_type=media,filename=file.name,background=None)
