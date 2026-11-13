"""Step 0 stub for the final stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class FinalStage(StubStage):
    name = "final"
    needs = ["simulation", "testplan"]
    reads_contract = False
