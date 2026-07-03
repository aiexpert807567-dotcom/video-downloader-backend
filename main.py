from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
import yt_dlp

app = FastAPI(title="Video Extractor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    url: str


class ExtractResponse(BaseModel):
    title: str
    thumbnail: str
    duration: int
    direct_url: str
    ext: str
    headers: Dict[str, str] = {}


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractResponse)
def extract(payload: ExtractRequest):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # acodec/vcodec != "none" ensures we only pick formats that already
        # have both audio and video combined (progressive), since we can't
        # merge separate streams without actually downloading server-side.
        "format": "best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]/best",
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract this video. It may be private, region-locked, or unsupported. ({str(e)})",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {str(e)}")

    if not info:
        raise HTTPException(status_code=422, detail="No video information found for this link")

    direct_url = info.get("url")
    matched_headers = None

    if not direct_url and info.get("formats"):
        last_format = info["formats"][-1]
        direct_url = last_format.get("url")
        matched_headers = last_format.get("http_headers")
    elif info.get("formats"):
        for f in info["formats"]:
            if f.get("url") == direct_url:
                matched_headers = f.get("http_headers")
                break

    if not direct_url:
        raise HTTPException(status_code=422, detail="Could not find a downloadable video URL for this link")

    headers = matched_headers or info.get("http_headers") or DEFAULT_HEADERS

    return ExtractResponse(
        title=info.get("title") or "Untitled",
        thumbnail=info.get("thumbnail") or "",
        duration=int(info.get("duration") or 0),
        direct_url=direct_url,
        ext=info.get("ext") or "mp4",
        headers=headers,
    )
