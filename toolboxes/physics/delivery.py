"""Presentation-only delivery sizing; canonical solver results remain unchanged."""
import shutil
from pathlib import Path

from physics_demo.io.video import encode_bounded_mp4, encode_watchable_mp4, VideoEncodingError


def publish_video(artifacts: Path, destination: Path, summary: dict,
                  max_bytes: int, timeout_seconds: float) -> dict:
    if type(max_bytes) is not int or max_bytes < 1024:
        raise VideoEncodingError('invalid host video byte limit')
    source = artifacts/'simulation.mp4'
    original = summary['artifacts']['video']
    if source.stat().st_size <= max_bytes:
        shutil.copyfile(source,destination)
        return {'max_bytes':max_bytes, 'bytes':destination.stat().st_size,
                'compressed':False, 'all_frames_preserved':True, 'solver_rerun':False}
    temporary = destination.with_name('.delivery-video.tmp.mp4')
    try:
        presentation = original.get('presentation')
        if presentation:
            metadata = encode_watchable_mp4(artifacts/'result.json', temporary,
                fps=int(original['fps']), segments=presentation['segments'],
                max_bytes=max_bytes, timeout_seconds=timeout_seconds)
        else:
            metadata = encode_bounded_mp4(artifacts/'result.json', temporary,
                int(original['fps']), max_bytes, timeout_seconds)
        if (metadata['sample_count'] != original['sample_count'] or
                abs(metadata['duration_s']-original['duration_s']) > 1e-6):
            raise VideoEncodingError('compressed video changed the frame count or playback duration')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {**metadata['delivery'], 'bytes':destination.stat().st_size,
            'source_bytes':source.stat().st_size, 'compressed':True,
            'codec':'jpeg', 'sample_count':metadata['sample_count'],
            'duration_s':metadata['duration_s']}
