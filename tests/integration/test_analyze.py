import pytest
from src import videoAnalyzer
import asyncio
import sample_list
import yt_dlp
import os
import hashlib
import json

DOWNLOAD_FOLDER = "./downloads/"
CACHE_FILE = "video_cache.json"

# Stelle sicher, dass der Download-Ordner existiert
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Lade den Cache (falls vorhanden)
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        video_cache = json.load(f)
else:
    video_cache = {}

def get_video_filename(youtube_url):
    """Erzeugt einen eindeutigen Dateinamen für die gegebene URL."""
    url_hash = hashlib.md5(youtube_url.encode()).hexdigest()
    return os.path.join(DOWNLOAD_FOLDER, f"{url_hash}.mp4")

async def download_video(youtube_url):
    """Lädt ein Video herunter, falls es noch nicht im Cache ist."""
    if youtube_url in video_cache:
        return video_cache[youtube_url]
    
    video_path = get_video_filename(youtube_url)
    
    ydl_opts = {
        "outtmpl": video_path,
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        'merge_output_format': 'mp4',
    }
    
    await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).download([youtube_url]))
    
    # Cache aktualisieren
    video_cache[youtube_url] = video_path
    with open(CACHE_FILE, "w") as f:
        json.dump(video_cache, f)
    
    return video_path

# Manuell auszuwählende Samples (falls None, werden alle verwendet)
SELECTED_SAMPLES = None  # Beispiel: [5, 7] für nur Sample 5 und 7

def get_selected_samples():
    """Ermöglicht die Auswahl einzelner Samples durch eine Liste im Code."""
    if SELECTED_SAMPLES is not None:
        return [sample for i, sample in enumerate(sample_list.samples) if i in SELECTED_SAMPLES]
    return sample_list.samples

@pytest.mark.parametrize("sample", get_selected_samples())
def test_count_timestamps(sample: sample_list.Sample, data_dir):
    path = asyncio.run(download_video(sample.url))
    video_analyzer = videoAnalyzer.VideoAnalyzer(path, debug=False)
    stable_rectangle = asyncio.run(video_analyzer.find_stable_rectangle(15000, 0))
    timestamps = asyncio.run(video_analyzer.analyze_video(stable_rectangle))
    
    assert len(timestamps) >= sample.count_oos
