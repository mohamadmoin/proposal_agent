"""The agentic engine (PROPOSAL_ENGINE=agentic): one lead researcher agent with a working memory, deterministic tools
and cheap sub-agents, built on pydantic-ai. It returns the same result contract as engine.pipeline, plus its trace
under result["agent"]. pipeline.run() dispatches here; the default engine stays the pipeline.
"""
