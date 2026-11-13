"""Step 0 stub for the firmware stage. Replaced by its owner seat."""

from engenia.stages.stub import StubStage


class FirmwareStage(StubStage):
    name = "firmware"
    needs = ["architect"]
    reads_contract = True
