"""Análise dos cortes: metadados, frames-chave, silêncios e transcrição."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class ClipInfo:
    id: str
    path: str
    filename: str
    duration: float
    width: int
    height: int
    fps: float
    rotation: int
    has_audio: bool
    hdr: bool = False
    silences: list[tuple[float, float]] = field(default_factory=list)
    scene_changes: list[float] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    frames: list[tuple[float, str]] = field(default_factory=list)  # (timestamp, jpg path)

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "ClipInfo":
        d = dict(d)
        d["words"] = [Word(**w) for w in d.get("words", [])]
        d["silences"] = [tuple(s) for s in d.get("silences", [])]
        d["frames"] = [tuple(f) for f in d.get("frames", [])]
        return ClipInfo(**d)

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    def transcript_lines(self, max_gap: float = 0.7) -> list[tuple[float, float, str]]:
        """Agrupa palavras em frases (quebra em pausas)."""
        lines, cur = [], []
        for w in self.words:
            if cur and (w.start - cur[-1].end > max_gap or len(cur) >= 14):
                lines.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
                cur = []
            cur.append(w)
        if cur:
            lines.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
        return lines


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    return json.loads(out.stdout)


def _fps(rate: str) -> float:
    try:
        n, d = rate.split("/")
        return float(n) / float(d) if float(d) else 30.0
    except Exception:
        return 30.0


def detect_silences(path: Path, noise_db: int = -35, min_dur: float = 0.5) -> list[tuple[float, float]]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    res = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        res.append((round(max(0.0, s), 2), round(e, 2) if e is not None else -1.0))
    return res


def detect_scenes(path: Path, threshold: float = 0.35) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [round(float(x), 2) for x in re.findall(r"pts_time:([\d.]+)", proc.stderr)][:40]


def extract_frames(path: Path, duration: float, out_dir: Path, clip_id: str, n: int) -> list[tuple[float, str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, n)
    frames = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        dst = out_dir / f"{clip_id}_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale='min(512,iw)':-2", "-q:v", "5", str(dst)],
            capture_output=True,
        )
        if dst.exists():
            frames.append((round(t, 2), str(dst)))
    return frames


_whisper_model = None


def transcribe(path: Path, language: str | None = "pt", model_size: str | None = None, log=print) -> list[Word]:
    """Transcrição com timestamps por palavra (faster-whisper). Retorna [] se indisponível."""
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("faster-whisper não instalado — seguindo sem transcrição.")
        return []
    try:
        model_size = model_size or os.environ.get("WHISPER_MODEL", "small")
        if _whisper_model is None:
            log(f"Carregando modelo Whisper '{model_size}' (primeira vez pode demorar)...")
            _whisper_model = WhisperModel(model_size, device="auto", compute_type="int8")
        segments, _ = _whisper_model.transcribe(
            str(path), language=language, word_timestamps=True, vad_filter=True
        )
        words = []
        for seg in segments:
            for w in seg.words or []:
                txt = w.word.strip()
                if txt:
                    words.append(Word(round(w.start, 2), round(w.end, 2), txt))
        return words
    except Exception as e:  # modelo não baixou, sem rede etc.
        log(f"Transcrição falhou ({e.__class__.__name__}: {e}) — seguindo sem transcrição.")
        return []


def analyze_clip(path: Path, clip_id: str, work_dir: Path, n_frames: int = 6,
                 do_transcribe: bool = True, language: str | None = "pt", log=print) -> ClipInfo:
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise ValueError(f"{path.name}: nenhum stream de vídeo encontrado")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    duration = float(info["format"].get("duration") or v.get("duration") or 0)
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 360
    if "rotate" in (v.get("tags") or {}):
        rotation = abs(int(v["tags"]["rotate"])) % 360

    hdr = v.get("color_transfer") in ("smpte2084", "arib-std-b67")
    clip = ClipInfo(
        hdr=hdr, id=clip_id, path=str(path), filename=path.name, duration=round(duration, 2),
        width=int(v["width"]), height=int(v["height"]), fps=round(_fps(v.get("avg_frame_rate", "30/1")), 2),
        rotation=rotation, has_audio=has_audio,
    )
    log(f"[{clip_id}] {path.name}: {clip.duration:.1f}s {clip.display_size[0]}x{clip.display_size[1]}")
    clip.frames = extract_frames(path, duration, work_dir / "frames", clip_id, n_frames)
    clip.scene_changes = detect_scenes(path)
    if has_audio:
        clip.silences = detect_silences(path)
        if do_transcribe:
            clip.words = transcribe(path, language=language, log=log)
            log(f"[{clip_id}] {len(clip.words)} palavras transcritas")
    return clip


def analyze_all(paths: list[Path], work_dir: Path, max_total_frames: int = 60,
                do_transcribe: bool = True, language: str | None = "pt", log=print) -> list[ClipInfo]:
    per_clip = max(2, min(8, max_total_frames // max(1, len(paths))))
    return [
        analyze_clip(p, f"C{i + 1}", work_dir, per_clip, do_transcribe, language, log)
        for i, p in enumerate(paths)
    ]
