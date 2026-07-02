from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Video Extractor API")

# Wide-open CORS since this is called directly from a mobile app,
# not a browser page that needs origin restrictions.
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


@app.get("/")
def root():
    # Simple health check endpoint. Cloud Run and your own testing
    # both use this to confirm the service is alive.
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
        "format": "best[ext=mp4]/best",
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
    if not direct_url and info.get("formats"):
        # Fall back to the last (usually highest quality progressive) format
        direct_url = info["formats"][-1].get("url")

    if not direct_url:
        raise HTTPException(status_code=422, detail="Could not find a downloadable video URL for this link")

    return ExtractResponse(
        title=info.get("title") or "Untitled",
        thumbnail=info.get("thumbnail") or "",
        duration=int(info.get("duration") or 0),
        direct_url=direct_url,
        ext=info.get("ext") or "mp4",
    )
