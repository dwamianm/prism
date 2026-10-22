"""The result of one ``MemoryEngine.reembed()`` call (dwamianm/prism#368)."""

from pydantic import BaseModel, ConfigDict, Field


class ReembedResult(BaseModel):
    """What one ``reembed()`` call looked at, did and left.

    A memory counts when it is active and has text to embed, the set ``rebuild_indexes()`` also indexes.
    ``total`` is how many there are in the scope of the call (one owner's, or the whole store), ``done`` how many
    of them have a vector under the model after the call, and ``remaining`` how many do not: ``total`` is ``done``
    plus ``remaining``. A call that finds nothing left reports ``remaining`` 0, and that is when the model's index
    is complete and ``embedding.model_name`` can be set to it.

    ``selected`` is what the call took up to its ``limit``, in id order, and ``processed`` how many vectors it
    wrote (fewer than ``selected`` when a memory was retired or got its vector from a concurrent write meanwhile).
    ``failed`` counts memories whose text could not be embedded or stored in this call; they stay in
    ``remaining`` and the next call tries them again, unless it continues from ``cursor``, which continues after
    the last selected memory and is ``None`` when none is left after it. A dry run takes nothing and writes
    nothing: ``selected`` is what a run would take and ``processed`` is 0.
    """

    model_config = ConfigDict(frozen=True)

    embedding_model: str
    embedding_version: str
    dimension: int = Field(ge=1)
    active: bool = Field(description="Whether the model is the engine's active model, the one searches read")
    user_id: str | None = None
    dry_run: bool = False
    total: int = Field(ge=0)
    done: int = Field(ge=0)
    remaining: int = Field(ge=0)
    selected: int = Field(ge=0)
    processed: int = Field(ge=0)
    failed: int = Field(ge=0)
    batches: int = Field(default=0, ge=0)
    cursor: str | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0)
