"""Uso:
  python -m editor.cli clip1.mp4 clip2.mp4 --brief "Reels energético do evento" --aspect 9:16 --seconds 45 -o projeto/
  python -m editor.cli --project projeto/ --revise "deixe mais curto e tire o trecho do palco"
  python -m editor.cli --project projeto/ --render-only        # re-renderiza plan.json (editado à mão)
"""
import argparse
from pathlib import Path

from .pipeline import Project


def main():
    ap = argparse.ArgumentParser(description="Editor de vídeo com IA")
    ap.add_argument("clips", nargs="*", type=Path, help="arquivos de vídeo (seus cortes)")
    ap.add_argument("-o", "--project", type=Path, default=Path("projeto"), help="pasta do projeto")
    ap.add_argument("--brief", default="", help="o que você quer: estilo, público, mensagem, CTA...")
    ap.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1", "4:5"])
    ap.add_argument("--seconds", type=float, default=None, help="duração-alvo")
    ap.add_argument("--music", type=Path, default=None, help="trilha sonora (mp3/wav)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--language", default="pt")
    ap.add_argument("--revise", default=None, help="pedido de ajuste sobre o plano atual")
    ap.add_argument("--plan-only", action="store_true", help="só gera plan.json, não renderiza")
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()

    proj = Project(a.project)
    if a.clips:
        proj.add_inputs(a.clips, a.music, {
            "brief": a.brief, "aspect": a.aspect, "target_seconds": a.seconds,
            "captions": not a.no_captions, "transcribe": not a.no_transcribe, "language": a.language,
        })
        proj.analyze()
    if not a.render_only:
        if a.revise:
            proj.make_plan(feedback=a.revise)
        elif a.clips or proj.plan is None:
            proj.make_plan()
        print(f"Plano salvo em {proj.root / 'plan.json'}")
    if not a.plan_only:
        proj.render()


if __name__ == "__main__":
    main()
