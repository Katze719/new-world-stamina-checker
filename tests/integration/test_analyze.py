import pytest
from src import videoAnalyzer
import asyncio


def test_one():
    assert True == True



def test_count_timestamps(data_dir):
    video_analyzer = videoAnalyzer.VideoAnalyzer(data_dir / "vods" / "1.mp4", debug=False)
    stable_rectangle = asyncio.run(video_analyzer.find_stable_rectangle(15000, 0))
    timestamps = asyncio.run(video_analyzer.analyze_video(stable_rectangle))

    assert len(timestamps) > 0