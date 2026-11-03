"""Timed overlays and FFmpeg rendering, independent of Django and inference."""

import json
import math
import subprocess
import textwrap
import time
import threading
from pathlib import Path


class RenderCancelled(Exception):
    pass


def scalar_intervals(times, values, delta_time, start, end):
    """Hold samples only for their validity interval; never bridge missing data."""
    samples = {}
    for timestamp, value in zip(times, values):
        timestamp, value = float(timestamp), float(value)
        if math.isfinite(timestamp):
            samples[timestamp] = value
    ordered = sorted(samples)
    if delta_time is None or not math.isfinite(float(delta_time)) or delta_time <= 0:
        gaps = sorted(b - a for a, b in zip(ordered, ordered[1:]) if b > a)
        delta_time = gaps[len(gaps) // 2] if gaps else 0.5
    intervals = []
    for index, timestamp in enumerate(ordered):
        stop = min(timestamp + delta_time, ordered[index + 1] if index + 1 < len(ordered) else end)
        left, right = max(start, timestamp), min(end, stop)
        value = samples[timestamp]
        if right > left and math.isfinite(value):
            intervals.append([left - start, right - start, f"{value:.3g}"])
    return intervals


def merged_intervals(intervals, duration, empty="—"):
    """Sweep interval boundaries to display overlapping labels without collisions."""
    boundaries = {0.0: [], duration: []}
    for index, (start, end, label) in enumerate(intervals):
        start, end = max(0, start), min(duration, end)
        if end <= start:
            continue
        boundaries.setdefault(start, []).append((index, label))
        boundaries.setdefault(end, []).append((index, None))
    active = {}
    previous = 0.0
    output = []
    for timestamp in sorted(boundaries):
        if timestamp > previous:
            text = " · ".join(dict.fromkeys(active.values())) or empty
            if output and output[-1][2] == text:
                output[-1][1] = timestamp
            else:
                output.append([previous, timestamp, text])
        for index, label in boundaries[timestamp]:
            if label is None:
                active.pop(index, None)
            else:
                active[index] = label
        previous = timestamp
    return output


def ass_time(seconds):
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"


def safe_text(value, columns, lines):
    # User labels cannot inject ASS override tags, drawings or line escapes.
    value = " ".join(str(value).split()).replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    wrapped = textwrap.wrap(value, width=columns) or [""]
    if len(wrapped) > lines:
        wrapped = wrapped[:lines]
        wrapped[-1] = wrapped[-1][:-1] + "…"
    return r"\N".join(wrapped)


def write_ass(snapshot, path, width, height):
    duration = snapshot["render_end"] - snapshot["start"]
    font_size = max(14, round(min(width, height) * 0.029 * snapshot["font_scale"]))
    margin = max(12, round(min(width, height) * 0.025))
    row_height = round(font_size * 2.7)
    columns = max(18, int((width - 2 * margin) / (font_size * 0.65)))
    panel_layers = [layer for layer in snapshot["layers"] if layer["mode"] in ("scalar", "label")]
    panel_height = margin * 2 + row_height * len(panel_layers) if panel_layers else 0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Panel,DejaVu Sans,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,{margin},{margin},{margin},1
Style: Caption,DejaVu Sans,{font_size},&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,3,4,0,2,{margin},{margin},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    row = 0
    for layer in snapshot["layers"]:
        if "intervals" not in layer:
            continue
        caption = layer["mode"] == "caption"
        color = layer["color"].lstrip("#")
        bgr = color[4:6] + color[2:4] + color[:2]
        position = "" if caption else f"\\pos({margin},{margin + row * row_height})"
        if not caption:
            row += 1
        for start, end, text in merged_intervals(layer["intervals"], duration, empty="" if caption else "—"):
            if not text or ass_time(start) == ass_time(end):
                continue
            if not caption:
                text = f"{layer['label']}: {text}"
            text = safe_text(text, columns, 3 if caption else 2)
            style = "Caption" if caption else "Panel"
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{{{position}\\c&H{bgr}&}}{text}\n")
    Path(path).write_text(header + "".join(events), encoding="utf-8")
    return panel_height


def probe_video(source, ffprobe="ffprobe", include_rotation=False):
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(source)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    streams = json.loads(result.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    width, height = video["width"], video["height"]
    sar = video.get("sample_aspect_ratio", "1:1").split(":")
    if len(sar) == 2 and int(sar[0]) > 0 and int(sar[1]) > 0:
        width = round(width * int(sar[0]) / int(sar[1]))
    rotation = float(video.get("tags", {}).get("rotate", 0))
    for side in video.get("side_data_list", []):
        rotation = float(side.get("rotation", rotation))
    if round(rotation) % 180:
        width, height = height, width
    return (width, height, rotation) if include_rotation else (width, height)


def render_video(source, snapshot, directory, progress=None, cancelled=None, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Stream frames through FFmpeg; preserve source timing and optional audio."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(source).resolve()
    if cancelled and cancelled():
        raise RenderCancelled()
    width, height, rotation = probe_video(source, ffprobe, include_rotation=True)
    scale = min(1, snapshot["max_dimension"] / max(width, height))
    width, height = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
    panel_height = write_ass(snapshot, directory / "overlay.ass", width, height)
    filters = [f"scale={width}:{height}", "setsar=1"]
    if panel_height:
        filters.append(f"drawbox=x=0:y=0:w=iw:h={panel_height}:color=black@{snapshot['opacity']}:t=fill")
    # Fixed relative paths avoid filter-language escaping of user filenames.
    filters.append("ass=overlay.ass")
    duration = snapshot["render_end"] - snapshot["start"]
    spatial = any("records" in layer for layer in snapshot["layers"])
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-ss", str(snapshot["start"]), "-i", str(source)]
    if spatial:
        from backend.utils.visualization_compositor import OverlayScene
        scene = OverlayScene(snapshot, width, height, rotation, panel_height)
        command += ["-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}",
                    "-framerate", "25", "-i", "pipe:0", "-filter_complex_threads", "1",
                    "-filter_complex", f"[0:v]{','.join(filters[:2])}[base];[base][1:v]overlay=eof_action=pass:repeatlast=0,{','.join(filters[2:])}[v]",
                    "-map", "[v]"]
    else:
        command += ["-map", "0:v:0", "-vf", ",".join(filters)]
    command += ["-t", str(duration)]
    if snapshot["audio"]:
        command += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k"]
    else:
        command += ["-an"]
    command += ["-c:v", "libx264", "-preset", "fast", "-threads", "2",
                "-crf", "20", "-pix_fmt", "yuv420p", "-fps_mode", "vfr",
                "-movflags", "+faststart", "-progress", "progress.txt", "output.partial.mp4"]
    partial = directory / "output.partial.mp4"
    try:
        with (directory / "ffmpeg.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=directory, stdin=subprocess.PIPE if spatial else subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
            writer_errors = []
            def write_frames():
                try:
                    for frame in range(math.ceil(duration * 25)):
                        if process.poll() is not None:
                            break
                        process.stdin.write(scene.frame(frame / 25))
                except BrokenPipeError:
                    pass
                except Exception as error:
                    writer_errors.append(error)
                finally:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
            writer = threading.Thread(target=write_frames, daemon=True) if spatial else None
            if writer:
                writer.start()
            try:
                while process.poll() is None:
                    if cancelled and cancelled():
                        raise RenderCancelled()
                    if progress:
                        progress_path = directory / "progress.txt"
                        if progress_path.exists():
                            # Only read the recent progress records, even for long videos.
                            with progress_path.open("rb") as handle:
                                handle.seek(max(0, progress_path.stat().st_size - 4096))
                                recent = handle.read().decode("utf-8", errors="replace")
                            values = [line.split("=", 1)[1] for line in recent.splitlines() if line.startswith("out_time_us=")]
                            if values and values[-1].isdigit():
                                progress(min(0.99, int(values[-1]) / 1_000_000 / duration))
                    time.sleep(0.25)
                if writer:
                    writer.join(timeout=5)
                if writer_errors:
                    raise RuntimeError("Could not draw the spatial overlays") from writer_errors[0]
                if process.returncode:
                    raise RuntimeError("FFmpeg could not render the video; see the export's ffmpeg.log")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if writer:
                    writer.join(timeout=5)
        if cancelled and cancelled():
            raise RenderCancelled()
        partial.replace(directory / "output.mp4")
        return directory / "output.mp4"
    finally:
        partial.unlink(missing_ok=True)
