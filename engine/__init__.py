"""The proposal-writing engine: plain Python, no Django imports.

Use `engine.pipeline.run(request, store, llm, pubmed, now)`; `engine.Store` is the storage interface.
"""

from engine.store import MemoryStore, Store

__all__ = ["MemoryStore", "Store"]
