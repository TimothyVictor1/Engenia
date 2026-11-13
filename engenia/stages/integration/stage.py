"""Step 0 stub for the integration stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class IntegrationStage(StubStage):
    name = "integration"
    needs = ["mechanical", "electronics", "firmware"]
    reads_contract = True
