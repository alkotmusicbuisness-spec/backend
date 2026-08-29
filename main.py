
import os,re,shutil,tempfile,zipfile
from pathlib import Path
from urllib.parse import urlparse
import yt_dlp
from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app=FastAPI(title="StreamGrab API",version="2.2.0")
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS","*").split(",")],allow_credentials=False,allow_methods=["*"],allow_headers=["*"])

def valid(u):
    p=urlparse(u); return p.scheme in ("http","https") and bool(p.netloc)

def extract(u,**kw):
    if not valid(u): raise HTTPException(400,"Invalid URL")
    try:
        with yt_dlp.YoutubeDL({"quiet":True,"no_warnings":True,"retries":3,"socket_timeout":30,**kw}) as y:
            return y.extract_info(u,download=False)
    except Exception as e:
        raise HTTPException(502,f"YouTube extraction failed: {e}")

@app.get("/")
def root():
    return {"status":"ok","service":"StreamGrab API","version":"2.2.0"}

@app.get("/api/analyze")
def analyze(url:str):
    info=extract(url,noplaylist=False)
    if info.get("_type")=="playlist" or info.get("entries") is not None:
        entries=[]
        for e in info.get("entries") or []:
            if e: entries.append({"id":e.get("id"),"title":e.get("title") or "Untitled","url":e.get("webpage_url") or e.get("url"),"thumbnail":e.get("thumbnail"),"max_height":e.get("height")})
        return {"kind":"playlist","title":info.get("title") or "Playlist","thumbnail":info.get("thumbnail"),"webpage_url":info.get("webpage_url") or url,"entry_count":len(entries),"entries":entries}
    vf=[]; af=[]; vh=set(); aid=set()
    for f in info.get("formats",[]):
        fid=f.get("format_id"); h=f.get("height"); vc=f.get("vcodec"); ac=f.get("acodec")
        if fid and vc and vc!="none" and h and h not in vh:
            vf.append({"format_id":fid,"height":h,"label":f"{h}p • {f.get('ext','?')}","has_audio":bool(ac and ac!="none")}); vh.add(h)
        if fid and ac and ac!="none" and fid not in aid:
            abr=f.get("abr") or 0; af.append({"format_id":fid,"abr":abr,"label":f"{round(abr)} kbps • {f.get('ext','?')}"}); aid.add(fid)
    vf.sort(key=lambda x:(x["height"],x["has_audio"]),reverse=True); af.sort(key=lambda x:x["abr"],reverse=True)
    return {"kind":"video","title":info.get("title") or "Video","thumbnail":info.get("thumbnail"),"duration":info.get("duration"),"uploader":info.get("uploader"),"webpage_url":info.get("webpage_url") or url,"video_formats":vf,"audio_formats":af}

@app.get("/api/formats")
def formats(url:str): return analyze(url)

@app.get("/api/download")
def download(url:str,format_id:str,type:str="mp4"):
    if type not in ("mp4","mp3"): raise HTTPException(400,"type must be mp4 or mp3")
    info=extract(url,noplaylist=True)
    title=re.sub(r"[^A-Za-z0-9._ -]","",info.get("title","download"))[:100].strip() or "download"
    temp=Path(tempfile.mkdtemp(prefix="streamgrab-")); out=temp/(title+".%(ext)s")
    opts={"quiet":True,"no_warnings":True,"noplaylist":True,"outtmpl":str(out),"format":format_id if type=="mp3" else f"{format_id}+bestaudio/best","merge_output_format":"mp4" if type=="mp4" else None}
    if type=="mp3": opts["postprocessors"]=[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"0"}]
    try:
        with yt_dlp.YoutubeDL(opts) as y: y.download([url])
    except Exception as e:
        shutil.rmtree(temp,ignore_errors=True); raise HTTPException(500,f"Download failed: {e}")
    files=[p for p in temp.iterdir() if p.is_file()]
    if not files: shutil.rmtree(temp,ignore_errors=True); raise HTTPException(500,"No output file was created")
    f=files[0]
    return FileResponse(f,media_type="audio/mpeg" if f.suffix.lower()==".mp3" else "video/mp4",filename=f.name,background=BackgroundTask(shutil.rmtree,temp,ignore_errors=True))

@app.get("/api/playlist/download")
def playlist_download(url:str,type:str="mp4",quality:str="best"):
    if type not in ("mp4","mp3"): raise HTTPException(400,"type must be mp4 or mp3")
    temp=Path(tempfile.mkdtemp(prefix="streamgrab-playlist-")); media=temp/"files"; media.mkdir(); archive=temp/"playlist.zip"
    try:
        if type=="mp4":
            try:q=int(quality)
            except:q=99999
            fmt=f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"; post=[]
        else:
            fmt="bestaudio/best"; post=[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":str(quality)}]
        opts={"quiet":True,"no_warnings":True,"noplaylist":False,"ignoreerrors":True,"format":fmt,"outtmpl":str(media/"%(playlist_index)03d - %(title).180s.%(ext)s"),"postprocessors":post,"merge_output_format":"mp4" if type=="mp4" else None}
        with yt_dlp.YoutubeDL(opts) as y: info=y.extract_info(url,download=True)
        files=[p for p in media.rglob("*") if p.is_file()]
        if not files: raise RuntimeError("No playlist files were created")
        with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
            for f in sorted(files): z.write(f,arcname=f.name)
        name=re.sub(r"[^A-Za-z0-9._ -]","",(info or {}).get("title","playlist"))[:80].strip() or "playlist"
        return FileResponse(archive,media_type="application/zip",filename=name+".zip",background=BackgroundTask(shutil.rmtree,temp,ignore_errors=True))
    except Exception as e:
        shutil.rmtree(temp,ignore_errors=True); raise HTTPException(500,f"Playlist download failed: {e}")
