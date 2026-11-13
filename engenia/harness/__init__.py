"""Agent harness: LLM client, sandboxed tools, limits, the agent loop and the design loop.

Nothing in here decides pass or fail from model output. Pass and fail come only from
`Verdict.result` values produced by verifier scripts.
"""
