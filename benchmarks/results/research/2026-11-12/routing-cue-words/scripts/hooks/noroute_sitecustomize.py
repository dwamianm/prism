# Task 27 scratch: with PRME_T27_NO_ROUTE=1, every question is left unrouted (route_question returns None),
# so address-first mode reads every question through the catalog walk. Research instrumentation only.
import os

if os.environ.get("PRME_T27_NO_ROUTE") == "1":
    import prme.retrieval.pipeline as _pipeline

    _pipeline.route_question = lambda *args, **kwargs: None
