"""Step 0 stub for the electronics stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class ElectronicsStage(StubStage):
    name = "electronics"
    needs = ["architect"]
    reads_contract = True
