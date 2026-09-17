"""External integrations for PRME.

LangChain and LlamaIndex require their framework as an optional dependency:

    pip install prme[langchain]    # LangChain adapter
    pip install prme[llamaindex]   # LlamaIndex adapter

The TypeSafe Jev product-alignment advisor uses PRME's core dependencies and a
caller-supplied credential. It remains opt-in and never performs entity merges.
"""
