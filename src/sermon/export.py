"""Export highlights as an FCP7 XML (XMEML) timeline for DaVinci Resolve / Premiere."""

import json
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote


def probe_video(video: Path) -> dict:
    """Read fps, dimensions, duration, and audio layout via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(video)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    info = json.loads(out)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    astream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    rate = vstream.get("avg_frame_rate", "0/0")
    if rate in ("0/0", "0", ""):
        rate = vstream["r_frame_rate"]
    fps = Fraction(rate)
    return {
        "fps": fps,
        "width": int(vstream["width"]),
        "height": int(vstream["height"]),
        "duration": float(info["format"]["duration"]),
        "has_audio": astream is not None,
        "audio_rate": int(astream["sample_rate"]) if astream else 48000,
        "audio_channels": int(astream.get("channels", 2)) if astream else 0,
    }


def _timeline_pieces(highlights: list[dict]) -> list[tuple[str, float, float]]:
    """Flatten highlights into timeline clips: hook first, then the full clip."""
    pieces = []
    for i, h in enumerate(highlights, 1):
        score = f"({h['virality_score']}) " if h.get("virality_score") is not None else ""
        if h.get("hook_start_sec") is not None:
            pieces.append((f"{i}. HOOK — {h['title']}", h["hook_start_sec"], h["hook_end_sec"]))
        pieces.append((f"{i}. {score}{h['title']}", h["start_sec"], h["end_sec"]))
    return pieces


def write_resolve_xml(highlights: list[dict], video: Path, out_path: Path) -> Path:
    """Write an XMEML timeline with a hook clip + full clip per highlight, in list order,
    all referencing the original video file."""
    meta = probe_video(video)
    fps: Fraction = meta["fps"]
    timebase = round(float(fps))
    ntsc = "TRUE" if fps.denominator == 1001 else "FALSE"
    channels = min(meta["audio_channels"], 2)

    def frames(sec: float) -> int:
        return round(sec * fps.numerator / fps.denominator)

    def rate_el(parent: ET.Element) -> None:
        rate = ET.SubElement(parent, "rate")
        ET.SubElement(rate, "timebase").text = str(timebase)
        ET.SubElement(rate, "ntsc").text = ntsc

    def file_el(parent: ET.Element, full: bool) -> None:
        file = ET.SubElement(parent, "file", id="file-1")
        if not full:  # later clipitems just reference the id
            return
        ET.SubElement(file, "name").text = video.name
        # FCP7-style host form — Resolve's XMEML importer doesn't reliably match file:/// paths
        ET.SubElement(file, "pathurl").text = "file://localhost" + quote(str(video.resolve()))
        rate_el(file)
        ET.SubElement(file, "duration").text = str(frames(meta["duration"]))
        # Resolve's importer silently rejects the whole XML when audio clipitems
        # reference a <file> that has no <timecode> block (found empirically —
        # video-only timelines import fine without it)
        tc = ET.SubElement(file, "timecode")
        ET.SubElement(tc, "string").text = "00:00:00:00"
        ET.SubElement(tc, "displayformat").text = "DF" if ntsc == "TRUE" else "NDF"
        rate_el(tc)
        media = ET.SubElement(file, "media")
        v = ET.SubElement(media, "video")
        sc = ET.SubElement(v, "samplecharacteristics")
        rate_el(sc)
        ET.SubElement(sc, "width").text = str(meta["width"])
        ET.SubElement(sc, "height").text = str(meta["height"])
        ET.SubElement(sc, "pixelaspectratio").text = "square"
        if meta["has_audio"]:
            a = ET.SubElement(media, "audio")
            asc = ET.SubElement(a, "samplecharacteristics")
            ET.SubElement(asc, "depth").text = "16"
            ET.SubElement(asc, "samplerate").text = str(meta["audio_rate"])
            ET.SubElement(a, "channelcount").text = str(meta["audio_channels"])

    def clipitem(
        parent: ET.Element,
        item_id: str,
        name: str,
        pos: int,
        length: int,
        src_in: int,
        first: bool,
        audio_channel: int | None,
        index: int,
    ) -> None:
        clip = ET.SubElement(parent, "clipitem", id=item_id)
        ET.SubElement(clip, "name").text = name
        ET.SubElement(clip, "enabled").text = "TRUE"
        ET.SubElement(clip, "duration").text = str(length)
        rate_el(clip)
        ET.SubElement(clip, "start").text = str(pos)
        ET.SubElement(clip, "end").text = str(pos + length)
        ET.SubElement(clip, "in").text = str(src_in)
        ET.SubElement(clip, "out").text = str(src_in + length)
        file_el(clip, full=first)
        if audio_channel is not None:
            st = ET.SubElement(clip, "sourcetrack")
            ET.SubElement(st, "mediatype").text = "audio"
            ET.SubElement(st, "trackindex").text = str(audio_channel)
        # link the video clip with its audio channel(s) so they move together
        refs = [(f"clipitem-v{index}", "video", 1)] + [
            (f"clipitem-a{index}c{ch}", "audio", ch) for ch in range(1, channels + 1)
        ]
        for ref_id, mediatype, trackindex in refs:
            link = ET.SubElement(clip, "link")
            ET.SubElement(link, "linkclipref").text = ref_id
            ET.SubElement(link, "mediatype").text = mediatype
            ET.SubElement(link, "trackindex").text = str(trackindex)
            ET.SubElement(link, "clipindex").text = str(index)

    pieces = _timeline_pieces(highlights)

    xmeml = ET.Element("xmeml", version="4")
    seq = ET.SubElement(xmeml, "sequence", id="sequence-1")
    ET.SubElement(seq, "name").text = f"{video.stem} highlights"
    total = sum(frames(end) - frames(start) for _, start, end in pieces)
    ET.SubElement(seq, "duration").text = str(total)
    rate_el(seq)

    media = ET.SubElement(seq, "media")
    video_el = ET.SubElement(media, "video")
    fmt = ET.SubElement(video_el, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    rate_el(sc)
    ET.SubElement(sc, "width").text = str(meta["width"])
    ET.SubElement(sc, "height").text = str(meta["height"])
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    vtrack = ET.SubElement(video_el, "track")

    atracks = []
    if channels:
        audio_el = ET.SubElement(media, "audio")
        afmt = ET.SubElement(audio_el, "format")
        asc = ET.SubElement(afmt, "samplecharacteristics")
        ET.SubElement(asc, "depth").text = "16"
        ET.SubElement(asc, "samplerate").text = str(meta["audio_rate"])
        atracks = [ET.SubElement(audio_el, "track") for _ in range(channels)]

    pos = 0
    for i, (name, start_sec, end_sec) in enumerate(pieces, 1):
        src_in = frames(start_sec)
        length = frames(end_sec) - src_in
        clipitem(vtrack, f"clipitem-v{i}", name, pos, length, src_in, first=(i == 1), audio_channel=None, index=i)
        for ch in range(1, channels + 1):
            clipitem(atracks[ch - 1], f"clipitem-a{i}c{ch}", name, pos, length, src_in, first=False, audio_channel=ch, index=i)
        pos += length

    tc = ET.SubElement(seq, "timecode")
    rate_el(tc)
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "DF" if ntsc == "TRUE" else "NDF"

    ET.indent(xmeml)
    out_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(xmeml, encoding="unicode") + "\n",
        encoding="utf-8",
    )
    return out_path


def export_timeline(highlights_json: Path, video: Path) -> Path:
    from . import layout, playable

    data = json.loads(highlights_json.read_text(encoding="utf-8"))
    if not data.get("highlights"):
        raise SystemExit(f"error: {highlights_json} contains no highlights")
    stem = highlights_json.name.removesuffix(".highlights.json")
    # Resolve links the file this XML names, and its importer has never read
    # Matroska — so a `.mkv` sermon is linked through its remuxed twin instead.
    # The twin is a stream copy, so every frame the timeline cuts on is the same one.
    media = playable.ensure(video, layout.playable_source(video))
    return write_resolve_xml(data["highlights"], media, highlights_json.parent / f"{stem}.resolve.xml")
