from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Dict
import yt_dlp
import requests
import re

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


def _referer_for(url: str) -> str:
    if "tiktok" in url:
        return "https://www.tiktok.com/"
    if "instagram" in url:
        return "https://www.instagram.com/"
    if "youtube" in url or "youtu.be" in url:
        return "https://www.youtube.com/"
    if "facebook" in url:
        return "https://www.facebook.com/"
    return url


def _extract(url: str):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
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

    # Merge in priority order: our safe defaults < yt-dlp's top-level
    # headers < yt-dlp's format-specific headers. Then guarantee a Referer
    # is always present, since several CDNs (TikTok especially) reject
    # requests missing it even when User-Agent is fine.
    headers = dict(DEFAULT_HEADERS)
    headers.update(info.get("http_headers") or {})
    headers.update(matched_headers or {})
    headers.setdefault("Referer", _referer_for(url))

    return info, direct_url, headers


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractResponse)
def extract(payload: ExtractRequest):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    info, direct_url, headers = _extract(url)

    return ExtractResponse(
        title=info.get("title") or "Untitled",
        thumbnail=info.get("thumbnail") or "",
        duration=int(info.get("duration") or 0),
        direct_url=direct_url,
        ext=info.get("ext") or "mp4",
        headers=headers,
    )


@app.post("/download")
def download(payload: ExtractRequest):
    """
    Streams the actual video bytes through this server instead of handing
    the client a raw CDN link. This is required because CDN links (YouTube
    especially) are IP-locked to whichever server requested them — a
    phone on a different network gets a 403 if it tries to fetch the raw
    link directly. Re-extracting here (same request, same IP) and proxying
    the bytes avoids that entirely.
    """
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    info, direct_url, headers = _extract(url)
    ext = info.get("ext") or "mp4"
    raw_title = info.get("title") or "video"
    safe_title = re.sub(r"[^a-zA-Z0-9]", "_", raw_title)
    filename = f"{safe_title}.{ext}"

    try:
        upstream = requests.get(direct_url, headers=headers, stream=True, timeout=30)
        upstream.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch video from source: {str(e)}")

    def iterfile():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    response_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    content_length = upstream.headers.get("Content-Length")
    if content_length:
        response_headers["Content-Length"] = content_length

    return StreamingResponse(
        iterfile(),
        media_type=f"video/{ext}",
        headers=response_headers,
    )
