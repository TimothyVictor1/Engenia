"""Step 0 stub for the mechanical stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class MechanicalStage(StubStage):
    name = "mechanical"
    needs = ["architect"]
    reads_contract = True
