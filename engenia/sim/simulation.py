"""Step 0 stub for the simulation stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class SimulationStage(StubStage):
    name = "simulation"
    needs = ["integration", "redteam", "simprep"]
    reads_contract = False
