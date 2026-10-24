from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from generation_contracts.validation import JobCreate, StrictDTO


def absent_default(schema: dict[str, Any]) -> None:
    schema.pop("default", None)


def workflow_options(value: dict[str, Any] | None, allowed: set[str]) -> dict[str, Any]:
    options = value or {}
    if not options.keys() <= allowed or any(item is None for item in options.values()):
        raise ValueError("Unsupported Workflow option")
    return options


def option_fields(input_data: dict[str, Any], parameters: dict[str, Any], options: dict[str, Any]) -> None:
    if "negative_prompt" in options:
        input_data["negative_prompt"] = options["negative_prompt"]
    parameters.update({name: options[name] for name in ("seed", "fps", "strength") if name in options})


class ImageGeneration(StrictDTO):
    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    n: Literal[1] = 1
    size: str | None = None
    quality: Literal["draft", "standard", "high"] | None = None
    response_format: Literal["url", "b64_json"] = "b64_json"
    user: str | None = Field(default=None, max_length=128)
    workflow_options: dict[str, Any] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)

    @field_validator("workflow_options", mode="before")
    @classmethod
    def nonnull_options(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Workflow options must be an object")
        return value

    def job(self, task_type: str = "text_to_image", extra_input: dict[str, Any] | None = None) -> JobCreate:
        body: dict[str, Any] = {"model": self.model, "task_type": task_type, "input": {"prompt": self.prompt, **(extra_input or {})}}
        parameters = {name: getattr(self, name) for name in ("n", "size", "quality") if name in self.model_fields_set and getattr(self, name) is not None}
        allowed = {"seed", "negative_prompt"} | ({"strength"} if task_type == "image_to_image" else set())
        option_fields(body["input"], parameters, workflow_options(self.workflow_options, allowed))
        if parameters:
            body["parameters"] = parameters
        if self.user is not None:
            body["client_request_id"] = self.user
        return JobCreate.model_validate(body)


class VideoGeneration(StrictDTO):
    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    size: str | None = None
    seconds: int | float | str | None = None
    quality: Literal["draft", "standard", "high"] | None = None
    references: list[dict[str, Any]] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)
    input_reference: dict[str, Any] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)
    workflow_options: dict[str, Any] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)

    @field_validator("workflow_options", "references", "input_reference", mode="before")
    @classmethod
    def nonnull_input(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Optional input cannot be null")
        return value

    def job(self) -> JobCreate:
        options = workflow_options(self.workflow_options, {"seed", "negative_prompt", "fps", "references"})
        references = self.references
        if "references" in options:
            if "references" in self.model_fields_set or not isinstance(options["references"], list) or not 2 <= len(options["references"]) <= 4:
                raise ValueError("Conflicting or invalid references")
            references = options["references"]
        if self.input_reference is not None:
            if references is not None or set(self.input_reference) != {"asset_id"}:
                raise ValueError("Conflicting or unresolved input reference")
            references = [{**self.input_reference, "role": "first_frame"}]
        body: dict[str, Any] = {"model": self.model, "task_type": "references_to_video" if references is not None else "text_to_video", "input": {"prompt": self.prompt}}
        if references is not None:
            body["input"]["references"] = references
        parameters = {name: getattr(self, name) for name in ("size", "quality", "seconds") if name in self.model_fields_set and getattr(self, name) is not None}
        if isinstance(parameters.get("seconds"), str):
            if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?", parameters["seconds"]):
                raise ValueError("Invalid duration")
            number = Decimal(parameters["seconds"])
            if not 0 < number <= 120:
                raise ValueError("Invalid duration")
            parameters["seconds"] = int(number) if number == int(number) else float(number)
        option_fields(body["input"], parameters, options)
        if parameters:
            body["parameters"] = parameters
        return JobCreate.model_validate(body)


class ImageEdit(ImageGeneration):
    source_image: dict[str, str] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)
    mask: dict[str, str] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)
    references: list[dict[str, Any]] | SkipJsonSchema[None] = Field(default=None, json_schema_extra=absent_default)

    @field_validator("source_image", "mask", "references", mode="before")
    @classmethod
    def nonnull_input(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Optional input cannot be null")
        return value

    def edit_job(self) -> JobCreate:
        fields = {name: getattr(self, name) for name in ("source_image", "mask", "references") if getattr(self, name) is not None}
        return self.job("references_to_image" if self.references is not None else "image_to_image", fields)
