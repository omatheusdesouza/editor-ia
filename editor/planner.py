"""Usa o Claude para "assistir" os cortes e produzir um plano de edição profissional."""
from __future__ import annotations

import base64
import json
import os

import anthropic

from .analyze import ClipInfo
from .plan import EditPlan, clamp_plan

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """Você é um editor de vídeo profissional sênior (redes sociais, eventos, institucional e YouTube).
Você recebe a análise de cortes brutos — frames amostrados com timestamp, transcrição com tempos,
silêncios e mudanças de cena — e devolve um plano de edição (EDL) que será renderizado automaticamente.

Como um bom editor, você:
- Abre com um gancho forte nos primeiros 2–3 segundos (a melhor fala, imagem ou momento).
- Constrói uma narrativa clara: gancho → contexto → desenvolvimento → clímax/mensagem → fechamento/CTA.
- Remove silêncios, hesitações, repetições, "éé", falsos começos e takes ruins (desfocados, tremidos, escuros).
- Corta falas em limites de palavra/frase usando os timestamps da transcrição (deixe ~0.1s de respiro
  antes e depois; nunca corte no meio de uma palavra).
- Varia o ritmo: planos de 1–4s para conteúdo dinâmico, mais longos para falas importantes.
- Usa cortes secos como padrão; transições só quando têm motivo (mudança de tempo/lugar, abertura, final).
- Usa câmera lenta só em momentos visuais de impacto e sem fala; acelera trechos longos sem fala, se útil.
- Usa textos na tela com moderação: título de abertura, nomes/lower thirds, frases-chave curtas.
- Escolhe 'blur_background' quando o enquadramento original não combina com o formato de saída e cortar
  perderia o assunto (ex.: vídeo horizontal com pessoas nas bordas em formato vertical); senão 'fill'.
- Respeita a duração-alvo pedida (tolerância de ~10%) e o estilo/briefing do usuário acima de tudo.

Regras técnicas:
- 'start'/'end' são tempos do clipe ORIGINAL e devem estar dentro da duração do clipe.
- Os tempos dos overlays são relativos ao início do segmento já com a velocidade aplicada.
- Duração de saída de um segmento = (end - start) / speed. Transições sobrepõem segmentos e encurtam o total.
- Um mesmo clipe pode ser usado em vários segmentos, em qualquer ordem.
- Escreva textos na tela e o resumo no idioma do briefing (padrão: português do Brasil)."""


def _fmt(t: float) -> str:
    return f"{t:.2f}"


def _clip_block(c: ClipInfo) -> str:
    w, h = c.display_size
    lines = [
        f"## Clipe {c.id} — arquivo '{c.filename}'",
        f"duração {c.duration:.2f}s | {w}x{h} | {c.fps:.0f}fps | áudio: {'sim' if c.has_audio else 'não'}",
    ]
    if c.scene_changes:
        lines.append("mudanças de cena (s): " + ", ".join(_fmt(t) for t in c.scene_changes))
    if c.silences:
        lines.append("silêncios (início–fim s): " + ", ".join(
            f"{_fmt(a)}–{_fmt(b) if b >= 0 else 'fim'}" for a, b in c.silences[:60]))
    tl = c.transcript_lines()
    if tl:
        lines.append("transcrição:")
        lines += [f"  [{_fmt(a)}–{_fmt(b)}] {txt}" for a, b, txt in tl]
    elif c.has_audio:
        lines.append("transcrição: (indisponível)")
    return "\n".join(lines)


def build_content(clips: list[ClipInfo], brief: str, aspect: str, target_seconds: float | None,
                  has_music: bool, captions_wanted: bool) -> list[dict]:
    content: list[dict] = [{
        "type": "text",
        "text": (
            "# Briefing do usuário\n" + (brief.strip() or "Edite como um editor profissional faria.") +
            f"\n\n# Saída\nformato {aspect}; duração-alvo: "
            f"{f'{target_seconds:.0f}s' if target_seconds else 'a que a história pedir'}; "
            f"trilha sonora: {'enviada pelo usuário' if has_music else 'nenhuma (music_volume será ignorado)'}; "
            f"legendas automáticas: {'desejadas' if captions_wanted else 'não (use captions=none)'}.\n\n"
            "# Cortes disponíveis"
        ),
    }]
    for c in clips:
        content.append({"type": "text", "text": _clip_block(c)})
        for t, path in c.frames:
            with open(path, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode()
            content.append({"type": "text", "text": f"{c.id} @ {_fmt(t)}s:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    return content


def make_plan(clips: list[ClipInfo], brief: str, aspect: str = "9:16", target_seconds: float | None = None,
              has_music: bool = False, captions_wanted: bool = True,
              previous: EditPlan | None = None, feedback: str | None = None,
              effort: str = "high", log=print) -> EditPlan:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log("Aviso: ANTHROPIC_API_KEY não definida — tentando perfil do 'ant auth login'.")
    try:
        client = anthropic.Anthropic()
    except Exception as e:
        raise RuntimeError("Sem credenciais da API do Claude. Defina ANTHROPIC_API_KEY "
                           "(crie uma chave em https://platform.claude.com).") from e
    content = build_content(clips, brief, aspect, target_seconds, has_music, captions_wanted)
    if previous is not None:
        content.append({
            "type": "text",
            "text": "# Plano anterior\n```json\n" + previous.model_dump_json(indent=1) + "\n```\n\n"
                    "# Ajustes pedidos pelo usuário\n" + (feedback or "") +
                    "\n\nDevolva o plano completo revisado, aplicando os ajustes e mantendo o que já estava bom.",
        })
    else:
        content.append({"type": "text", "text": "Monte o plano de edição completo."})

    log(f"Planejando edição com {MODEL}...")
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        output_format=EditPlan,
        # Se um classificador de segurança recusar por engano, o servidor tenta outro modelo automaticamente.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError(f"O modelo recusou o pedido: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Resposta cortada (max_tokens). Tente com menos clipes.")
    plan = msg.parsed_output
    if plan is None:
        text = next((b.text for b in msg.content if b.type == "text"), "")
        plan = EditPlan.model_validate(json.loads(text))
    log(f"Plano: {len(plan.segments)} segmentos — {plan.summary}")
    return clamp_plan(plan, {c.id: c for c in clips})
