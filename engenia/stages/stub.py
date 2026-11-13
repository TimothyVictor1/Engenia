"""Step 0 placeholder behaviour shared by every stub stage.

Each stub lives in the folder of the seat that will replace it. Real stages do not inherit
from this class; they implement the Stage protocol directly.
"""

from __future__ import annotations

import shutil

from engenia.contracts.models import StageContext

PLACEHOLDER_NAME = "placeholder.txt"


class StubStage:
    name: str
    needs: list[str] = []
    reads_contract: bool = False
    checks: list[str] = []

    def generate(self, ctx: StageContext) -> None:
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / PLACEHOLDER_NAME).write_text(f"stub: {self.name}\n", encoding="utf-8")

    def build(self, ctx: StageContext) -> None:
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        shutil.copytree(ctx.src_dir, ctx.build_dir)

    def interface(self, ctx: StageContext) -> dict:
        return {"stub": True}
