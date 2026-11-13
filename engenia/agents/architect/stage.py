"""Step 0 stub for the architect stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class ArchitectStage(StubStage):
    name = "architect"
    needs = ["verify_plan"]
    reads_contract = False
