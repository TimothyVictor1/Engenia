"""Step 0 stub for the report stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class ReportStage(StubStage):
    name = "report"
    needs = ["final"]
    reads_contract = False
