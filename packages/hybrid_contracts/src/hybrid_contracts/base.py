from typing import Annotated
from pydantic import BaseModel, ConfigDict, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "1.0"
