"""Shapes exchanged with the drafting model and returned to the application."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DraftSentence(BaseModel):
    """One sentence proposed by the model, with the passages and database facts it says support it."""
    model_config = ConfigDict(extra="forbid")

    text: str
    sources: list[int] = Field(default_factory=list,
                               description="Numbers of the passages that support this sentence.")
    # Named and described for the model: a plain "facts" field was read as
    # "the facts this sentence states" and filled with text.
    database_fact_ids: list[str] = Field(
        default_factory=list,
        description='Ids of database facts used in this sentence, e.g. "DB1". Empty when there are none.')


class Draft(BaseModel):
    """The model's structured reply. Untrusted until checked."""
    model_config = ConfigDict(extra="forbid")

    answerable: bool
    sentences: list[DraftSentence] = Field(default_factory=list)


@dataclass(frozen=True)
class Passage:
    """A passage as shown to the model: its text and where it comes from."""
    text: str
    source: str


@dataclass(frozen=True)
class Fact:
    """
    A database result the answer must state. The model sees only the
    statement, with the figure hidden behind {id}; the application puts the
    value back after the checks, so the model can never change a figure.
    """
    id: str          # e.g. "DB1"
    statement: str   # e.g. "The total balance for Blue Prism is {DB1}."
    value: str       # e.g. "24000.00 USD"


@dataclass(frozen=True)
class CheckedSentence:
    text: str                        # placeholders already replaced by their values
    sources: tuple[int, ...]         # 1-based passage numbers
    facts: tuple[str, ...] = ()      # database fact ids


@dataclass(frozen=True)
class WrittenAnswer:
    """
    status
        written       at least one sentence passed every check
        not_answered  the model said the passages do not answer the question
        unsupported   the model answered, but no sentence passed the checks
        failed        the model could not be reached or replied in the wrong shape
    """
    status: Literal["written", "not_answered", "unsupported", "failed"]
    sentences: tuple[CheckedSentence, ...] = ()
    dropped: tuple[str, ...] = ()  # one reason per sentence left out

    @property
    def text(self) -> str:
        """Sentences with their passage numbers, e.g. "Fees are 2% per month. [1]"."""
        return " ".join(
            f"{s.text} {''.join(f'[{n}]' for n in s.sources)}{'[database]' if s.facts else ''}"
            for s in self.sentences)
