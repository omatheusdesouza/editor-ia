"""Renderiza um EditPlan com FFmpeg: enquadramento, cor, movimento, legendas, textos, transições e trilha."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .analyze import ClipInfo, Word
from .plan import ASPECTS, EditPlan, Segment

FPS = 30
TIMESCALE = 15360

LOOKS = {
    "natural": "",
    "warm": "colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.04:bm=-0.04,eq=saturation=1.08",
    "cool": "colorbalance=rs=-0.05:bs=0.06:rm=-0.03:bm=0.04",
    "cinematic": "eq=contrast=1.08:saturation=0.92,colorbalance=rs=-0.04:bs=0.05:rh=0.05:bh=-0.04,vignette=PI/5",
    "black_white": "hue=s=0,eq=contrast=1.15",
    "vibrant": "eq=saturation=1.3:contrast=1.05",
    "vintage": "curves=preset=vintage,eq=saturation=0.85,vignette=PI/4",
}


# ---------------------------------------------------------------- utilidades

BUNDLED_FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Inter-Bold.otf"


def _font(bold: bool = True) -> str:
    if BUNDLED_FONT.exists():
        return str(BUNDLED_FONT)
    for q in (["Inter:bold", "Montserrat:bold", "Arial:bold", "DejaVu Sans:bold"] if bold
              else ["Inter", "Montserrat", "Arial", "DejaVu Sans"]):
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", q], capture_output=True, text=True).stdout.strip()
            if out and Path(out).exists():
                return out
        except FileNotFoundError:
            break
    for p in ("C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf"):
        if Path(p).exists():
            return p
    return ""


def _esc_path(p: str) -> str:
    """Escapa um caminho para uso dentro de uma opção de filtro do FFmpeg."""
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ffmpeg(args: list[str], cwd: Path, log) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        log("FFmpeg falhou:\n" + proc.stderr[-3000:])
        last = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "erro desconhecido"
        raise RuntimeError(f"FFmpeg falhou: {last}")


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


# ---------------------------------------------------------------- legendas (ASS)

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_text(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[Word], seg: Segment, W: int, H: int, style: str, font_name: str) -> str | None:
    dur = (seg.end - seg.start) / seg.speed
    ws = []
    for w in words:
        if w.end <= seg.start or w.start >= seg.end:
            continue
        a = max(0.0, (w.start - seg.start) / seg.speed)
        b = min(dur, (w.end - seg.start) / seg.speed)
        if b - a > 0.02:
            ws.append(Word(a, b, w.text))
    if not ws:
        return None

    vertical = H > W
    max_words = 3 if vertical else 6
    size = int(min(W, H) * (0.075 if vertical else 0.055))
    margin_v = int(H * (0.22 if vertical else 0.08))
    upper = style == "bold_highlight"

    chunks, cur = [], []
    for w in ws:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > 0.5 or cur[-1].text[-1:] in ".?!"):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(2, size // 12)},{max(1, size // 25)},2,{int(W * 0.08)},{int(W * 0.08)},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for ci, ch in enumerate(chunks):
        end_chunk = min(dur, max(ch[-1].end, ch[0].start + 0.4))
        if ci + 1 < len(chunks):
            end_chunk = min(end_chunk + 0.25, chunks[ci + 1][0].start)
        texts = [_ass_text(w.text.upper() if upper else w.text) for w in ch]
        if style == "bold_highlight":
            for wi, w in enumerate(ch):
                a = w.start if wi else ch[0].start
                b = ch[wi + 1].start if wi + 1 < len(ch) else end_chunk
                parts = [("{\\c&H00E5FF&\\fscx108\\fscy108}" + t + "{\\r}") if j == wi else t
                         for j, t in enumerate(texts)]
                events.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Cap,,0,0,0,,{' '.join(parts)}")
        else:
            events.append(f"Dialogue: 0,{_ass_time(ch[0].start)},{_ass_time(end_chunk)},Cap,,0,0,0,,{' '.join(texts)}")
    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------- textos na tela

def _drawtext(o, idx: int, seg_idx: int, W: int, H: int, work: Path, font: str) -> str:
    m = min(W, H)
    size = {"title": 0.085, "subtitle": 0.055, "label": 0.042}[o.style] * m
    txt_file = work / f"txt_{seg_idx}_{idx}.txt"
    txt_file.write_text(o.text.upper() if o.style == "title" else o.text, encoding="utf-8")
    a, b = o.start, o.start + o.duration
    alpha = f"if(lt(t,{a}+0.25),(t-{a})/0.25,if(gt(t,{b}-0.25),({b}-t)/0.25,1))"
    x = "(w-text_w)/2"
    y = {"top": "h*0.12", "center": "(h-text_h)/2", "bottom": "h*0.78-text_h", "lower_third": "h*0.72"}[o.position]
    if o.position == "lower_third":
        x = "w*0.07"
    box = ":box=1:boxcolor=black@0.55:boxborderw=" + str(int(size * 0.35)) if o.style == "label" else ""
    font_opt = f"fontfile='{_esc_path(font)}':" if font else ""
    return (
        f"drawtext={font_opt}textfile='{txt_file.name}':fontsize={int(size)}:fontcolor=white"
        f":shadowcolor=black@0.6:shadowx=3:shadowy=3:borderw={max(1, int(size / 28))}:bordercolor=black@0.5"
        f"{box}:x={x}:y={y}:enable='between(t,{a},{b})':alpha='{alpha}'"
    )


# ---------------------------------------------------------------- render por segmento

def render_segment(seg: Segment, idx: int, clip: ClipInfo, plan: EditPlan, W: int, H: int,
                   work: Path, captions_enabled: bool, log) -> Path:
    src_dur = seg.end - seg.start
    out_dur = src_dur / seg.speed
    font = _font(True)
    chains = []

    # Enquadramento
    base = f"[0:v]setpts=(PTS-STARTPTS)/{seg.speed:.4f}"
    if clip.hdr:  # vídeo HDR de celular (iPhone etc.): converte para SDR para não ficar "lavado"
        base += (",zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                 "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    if seg.framing == "blur_background":
        chains.append(f"{base},split[bgs][fgs]")
        chains.append(f"[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                      f"scale={W // 4}:{H // 4},gblur=sigma=12,scale={W}:{H},eq=brightness=-0.07[bg]")
        chains.append(f"[fgs]scale={W}:{H}:force_original_aspect_ratio=decrease[fg]")
        chains.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[framed]")
    else:
        chains.append(f"{base},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[framed]")

    vf = []
    if seg.motion == "punch_in":
        vf.append(f"scale={int(W * 1.12) // 2 * 2}:{int(H * 1.12) // 2 * 2},crop={W}:{H}")
    elif seg.motion == "slow_zoom_pan":
        vf.append(f"scale={int(W * 1.12) // 2 * 2}:{int(H * 1.12) // 2 * 2},"
                  f"crop={W}:{H}:x='(iw-ow)*min(t/{out_dur:.3f},1)':y='(ih-oh)/2'")
    vf.append(f"fps={FPS},format=yuv420p")
    if LOOKS.get(plan.look):
        vf.append(LOOKS[plan.look])
    if captions_enabled and plan.captions != "none" and seg.keep_audio and clip.words:
        ass = build_ass(clip.words, seg, W, H, plan.captions, _font_family(font))
        if ass:
            ass_path = work / f"cap_{idx}.ass"
            ass_path.write_text(ass, encoding="utf-8")
            fontsdir = f":fontsdir='{_esc_path(str(Path(font).parent))}'" if font else ""
            vf.append(f"subtitles='{ass_path.name}'{fontsdir}")
    for oi, o in enumerate(seg.overlays):
        vf.append(_drawtext(o, oi, idx, W, H, work, font))
    chains.append("[framed]" + ",".join(vf) + "[v]")

    if seg.keep_audio and clip.has_audio:
        chains.append(f"[0:a]asetpts=PTS-STARTPTS,atempo={seg.speed:.4f},"
                      "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a]")
    else:
        chains.append(f"[1:a]atrim=0:{out_dur:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[a]")

    out = work / f"seg_{idx:03d}.mp4"
    _ffmpeg([
        "-ss", f"{seg.start:.3f}", "-t", f"{src_dur:.3f}", "-i", clip.path,
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
        "-t", f"{out_dur:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
        "-video_track_timescale", str(TIMESCALE),
        "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
        out.name,
    ], cwd=work, log=log)
    return out


def _font_family(path: str) -> str:
    if not path:
        return "Arial"
    try:
        out = subprocess.run(["fc-query", "-f", "%{family[0]}", path], capture_output=True, text=True).stdout.strip()
        return out or "Arial"
    except FileNotFoundError:
        return "Arial"


# ---------------------------------------------------------------- montagem final

def render(plan: EditPlan, clips: list[ClipInfo], out_path: Path, aspect: str = "9:16",
           music: Path | None = None, work_dir: Path | None = None, captions_enabled: bool = True,
           log=print, progress=lambda f: None) -> Path:
    W, H = ASPECTS[aspect]
    by_id = {c.id: c for c in clips}
    work = Path(work_dir or out_path.parent / "_render")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    seg_files, durs = [], []
    for i, seg in enumerate(plan.segments):
        log(f"Renderizando segmento {i + 1}/{len(plan.segments)} ({seg.clip_id} {seg.start:.2f}–{seg.end:.2f}s)")
        f = render_segment(seg, i, by_id[seg.clip_id], plan, W, H, work, captions_enabled, log)
        seg_files.append(f)
        durs.append(_duration(f))
        progress(0.85 * (i + 1) / len(plan.segments))

    # Encadeia segmentos com cortes secos ou transições xfade/acrossfade.
    n = len(seg_files)
    fc = [f"[{i}:v]fps={FPS},format=yuv420p,setsar=1,settb=AVTB[v{i}];"
          f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]" for i in range(n)]
    cur_v, cur_a, total = "v0", "a0", durs[0]
    for i in range(1, n):
        seg = plan.segments[i]
        d = min(seg.transition_duration, durs[i] / 2.5, total / 2.5)
        if seg.transition_in == "cut" or d < 0.1:
            fc.append(f"[{cur_v}][{cur_a}][v{i}][a{i}]concat=n=2:v=1:a=1[cc{i}][ca{i}]")
            fc.append(f"[cc{i}]settb=AVTB[cv{i}]")
            total += durs[i]
        else:
            fc.append(f"[{cur_v}][v{i}]xfade=transition={seg.transition_in}:duration={d:.3f}:offset={total - d:.3f}[cv{i}]")
            fc.append(f"[{cur_a}][a{i}]acrossfade=d={d:.3f}[ca{i}]")
            total += durs[i] - d
        cur_v, cur_a = f"cv{i}", f"ca{i}"

    vpost = []
    if plan.fade_in:
        vpost.append("fade=t=in:st=0:d=0.5")
    if plan.fade_out:
        vpost.append(f"fade=t=out:st={max(0, total - 0.8):.3f}:d=0.8")
    fc.append(f"[{cur_v}]{','.join(vpost) or 'null'}[vout]")

    inputs = []
    for f in seg_files:
        inputs += ["-i", f.name]
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        vol = plan.music_volume
        fc.append(f"[{n}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                  f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol:.3f},"
                  f"afade=t=in:d=1,afade=t=out:st={max(0, total - 2):.3f}:d=2[mus]")
        if plan.duck_music_under_speech:
            fc.append(f"[{cur_a}]asplit[voice][sc]")
            fc.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[duck]")
            fc.append("[voice][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            fc.append(f"[{cur_a}][mus]amix=inputs=2:duration=first:normalize=0[mix]")
        cur_a = "mix"
    apost = ["loudnorm=I=-14:TP=-1.5:LRA=11", "aresample=48000"]
    if plan.fade_in:
        apost.append("afade=t=in:d=0.4")
    if plan.fade_out:
        apost.append(f"afade=t=out:st={max(0, total - 0.8):.3f}:d=0.8")
    fc.append(f"[{cur_a}]{','.join(apost)}[aout]")

    script = work / "graph.txt"
    script.write_text(";\n".join(fc), encoding="utf-8")
    log(f"Montagem final ({total:.1f}s, {W}x{H})...")
    out_abs = Path(out_path).resolve()
    _ffmpeg([*inputs, "-filter_complex_script", script.name, "-map", "[vout]", "-map", "[aout]",
             "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_abs)], cwd=work, log=log)
    progress(1.0)
    log(f"Pronto: {out_abs}")
    return out_abs
