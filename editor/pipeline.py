"""Orquestra: análise → plano (Claude) → render. Salva tudo numa pasta de projeto."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .analyze import ClipInfo, analyze_all
from .plan import EditPlan, clamp_plan
from .planner import make_plan
from .render import render


class Project:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clips_dir = self.root / "clips"
        self.meta_path = self.root / "project.json"
        self.meta = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}

    def save(self):
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1))

    # --- dados
    @property
    def clips(self) -> list[ClipInfo]:
        return [ClipInfo.from_json(c) for c in self.meta.get("clips", [])]

    @property
    def plan(self) -> EditPlan | None:
        p = self.root / "plan.json"
        return EditPlan.model_validate_json(p.read_text()) if p.exists() else None

    def set_plan(self, plan: EditPlan):
        (self.root / "plan.json").write_text(plan.model_dump_json(indent=1))
        history = self.root / "history"
        history.mkdir(exist_ok=True)
        (history / f"plan_v{len(list(history.glob('plan_v*.json'))) + 1}.json").write_text(plan.model_dump_json(indent=1))

    @property
    def music(self) -> Path | None:
        m = self.meta.get("music")
        return Path(m) if m else None

    @property
    def output(self) -> Path:
        return self.root / "video_final.mp4"

    # --- etapas
    def add_inputs(self, clip_paths: list[Path], music: Path | None, settings: dict, move: bool = False):
        self.clips_dir.mkdir(exist_ok=True)
        stored = []
        for p in clip_paths:
            dst = self.clips_dir / Path(p).name
            if Path(p).resolve() != dst.resolve():
                (shutil.move if move else shutil.copy2)(p, dst)
            stored.append(str(dst.resolve()))
        self.meta["clip_paths"] = stored
        if music:
            dst = self.root / ("music" + Path(music).suffix)
            if Path(music).resolve() != dst.resolve():
                (shutil.move if move else shutil.copy2)(music, dst)
            self.meta["music"] = str(dst.resolve())
        self.meta["settings"] = settings
        self.save()

    def analyze(self, log=print):
        s = self.meta["settings"]
        clips = analyze_all([Path(p) for p in self.meta["clip_paths"]], self.root / "analysis",
                            do_transcribe=s.get("transcribe", True), language=s.get("language") or None, log=log)
        self.meta["clips"] = [c.to_json() for c in clips]
        self.save()

    def make_plan(self, feedback: str | None = None, log=print) -> EditPlan:
        s = self.meta["settings"]
        plan = make_plan(self.clips, s.get("brief", ""), s.get("aspect", "9:16"), s.get("target_seconds"),
                         has_music=self.music is not None, captions_wanted=s.get("captions", True),
                         previous=self.plan if feedback else None, feedback=feedback, log=log)
        self.set_plan(plan)
        return plan

    def apply_manual_plan(self, plan: EditPlan):
        self.set_plan(clamp_plan(plan, {c.id: c for c in self.clips}))

    def render(self, log=print, progress=lambda f: None) -> Path:
        s = self.meta["settings"]
        return render(self.plan, self.clips, self.output, s.get("aspect", "9:16"), self.music,
                      self.root / "_render", captions_enabled=s.get("captions", True), log=log, progress=progress)
