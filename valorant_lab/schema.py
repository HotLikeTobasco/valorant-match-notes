from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Event(BaseModel):
    time: float = Field(ge=0, allow_inf_nan=False)
    kind: Literal["scene_change", "round_start", "round_end", "match_start", "match_end", "kill", "death", "shot", "utility", "engagement", "note"]
    confidence: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    source: str = Field(default="manual", max_length=100)
    note: str = Field(default="", max_length=1000)
    target_id: str | None = Field(default=None, max_length=100)
    tag: Literal["low_crosshair", "wide_swing", "utility_late", "untraded", "movement_shot"] | None = None


class Observation(BaseModel):
    time: float = Field(ge=0, allow_inf_nan=False)
    head_x: float = Field(ge=0, le=1, allow_inf_nan=False)
    head_y: float = Field(ge=0, le=1, allow_inf_nan=False)
    crosshair_x: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    crosshair_y: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    crosshair_source: Literal["assumed_center", "manual", "detected"] = "assumed_center"
    target_id: str = Field(min_length=1, max_length=100)
    confidence: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    source: Literal["manual", "onnx"] = "manual"


class Evidence(BaseModel):
    schema_version: Literal[1] = 1
    events: list[Event] = Field(default_factory=list, max_length=20000)
    observations: list[Observation] = Field(default_factory=list, max_length=100000)
    map_name: str = Field(default="Unknown", max_length=100)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def unique_observations(self):
        keys = [(o.time, o.target_id) for o in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate target observation at the same timestamp")
        return self

    def within(self, duration):
        if any(item.time > duration for item in [*self.events, *self.observations]):
            raise ValueError("evidence timestamps exceed the recording duration")
        return self
