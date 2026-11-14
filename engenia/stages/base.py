"""The Stage contract (PROJECT.md section 6)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engenia.contracts.models import StageContext


@runtime_checkable
class Stage(Protocol):
    name: str  # exact stage name from section 2
    needs: list[str]  # upstream stage names
    reads_contract: bool
    checks: list[str]  # verifier names run after build
    # Optional, default False: a stage that sets `main_thread = True` runs on the main thread,
    # alone (scheduler). Use it for gmsh and capytaine. Read with getattr, so it is not required.

    def generate(self, ctx: StageContext) -> None:
        """Agent writes src/<stage>/."""
        ...

    def build(self, ctx: StageContext) -> None:
        """Deterministic, no model calls."""
        ...

    def interface(self, ctx: StageContext) -> dict:
        """Facts for later stages."""
        ...
