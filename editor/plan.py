"""Esquema do plano de edição (EDL) que o Claude produz e o renderizador consome."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

Transition = Literal[
    "cut", "fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight",
    "slideleft", "slideup", "smoothleft", "circleopen", "zoomin",
]
Motion = Literal["none", "punch_in", "slow_zoom_pan"]
Framing = Literal["fill", "blur_background"]
Look = Literal["natural", "warm", "cool", "cinematic", "black_white", "vibrant", "vintage"]
TextPos = Literal["top", "center", "bottom", "lower_third"]
TextStyle = Literal["title", "subtitle", "label"]
CaptionStyle = Literal["none", "clean", "bold_highlight"]


class TextOverlay(BaseModel):
    text: str = Field(description="Texto curto exibido na tela (máx ~6 palavras).")
    position: TextPos
    style: TextStyle
    start: float = Field(description="Segundos a partir do início DESTE segmento (tempo de saída).")
    duration: float = Field(description="Duração em segundos.")


class Segment(BaseModel):
    clip_id: str = Field(description="ID do clipe de origem, ex: C1")
    start: float = Field(description="Ponto de entrada no clipe de origem (segundos).")
    end: float = Field(description="Ponto de saída no clipe de origem (segundos).")
    speed: float = Field(description="1.0 = normal. 0.5 = câmera lenta. 1.5 = acelerado. Entre 0.5 e 2.0.")
    transition_in: Transition = Field(description="Transição vinda do segmento anterior ('cut' = corte seco).")
    transition_duration: float = Field(description="Duração da transição (0.2–1.0s). Ignorado se 'cut'.")
    motion: Motion
    framing: Framing = Field(description="'fill' corta para preencher; 'blur_background' encaixa sobre fundo desfocado.")
    keep_audio: bool = Field(description="Manter o áudio original do trecho.")
    overlays: List[TextOverlay]
    reason: str = Field(description="Por que este trecho foi escolhido (1 frase).")


class EditPlan(BaseModel):
    title: str
    summary: str = Field(description="Resumo da narrativa / decisões de edição, em português.")
    look: Look
    captions: CaptionStyle
    music_volume: float = Field(description="Volume da trilha (0–1). 0.25 é bom sob fala; 0.8 sem fala.")
    duck_music_under_speech: bool
    fade_in: bool
    fade_out: bool
    segments: List[Segment]


ASPECTS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def clamp_plan(plan: EditPlan, clips: dict) -> EditPlan:
    """Corrige valores fora da faixa e trechos que excedem a duração do clipe."""
    fixed = []
    for s in plan.segments:
        clip = clips.get(s.clip_id)
        if clip is None:
            continue
        s.start = max(0.0, min(s.start, clip.duration - 0.2))
        s.end = max(s.start + 0.2, min(s.end, clip.duration))
        s.speed = min(2.0, max(0.5, s.speed or 1.0))
        out_dur = (s.end - s.start) / s.speed
        s.transition_duration = min(max(0.15, s.transition_duration), 1.0, out_dur / 2.5)
        for o in s.overlays:
            o.start = max(0.0, min(o.start, out_dur - 0.3))
            o.duration = max(0.3, min(o.duration, out_dur - o.start))
        fixed.append(s)
    if not fixed:
        raise ValueError("O plano não tem nenhum segmento válido.")
    fixed[0].transition_in = "cut"
    plan.segments = fixed
    plan.music_volume = min(1.0, max(0.0, plan.music_volume))
    return plan
