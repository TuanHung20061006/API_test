from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


PreferenceListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
AdviceListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
PackingListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
AIAdviceSeverity = Literal["low", "medium", "high"]


class _StrictSchemaModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class AIAdvicePreferences(_StrictSchemaModel):
    budget: str | None = Field(default=None, min_length=1, max_length=30)
    pace: str | None = Field(default=None, min_length=1, max_length=30)
    interests: list[PreferenceListItem] = Field(
        default_factory=list,
        max_length=20,
    )
    transport: str | None = Field(default=None, min_length=1, max_length=50)
    dietary_requirements: list[PreferenceListItem] = Field(
        default_factory=list,
        max_length=20,
    )
    notes: str | None = Field(default=None, min_length=1, max_length=1000)


class AIAdviceRequest(_StrictSchemaModel):
    mode: Literal["review"] = "review"
    language: str = Field(
        default="vi",
        min_length=2,
        max_length=10,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?$",
    )
    include_weather: bool = True
    regenerate: bool = False
    preferences: AIAdvicePreferences = Field(default_factory=AIAdvicePreferences)


class AIAdviceIssue(_StrictSchemaModel):
    severity: AIAdviceSeverity
    day: int | None = Field(default=None, ge=1, le=366)
    message: str = Field(min_length=1, max_length=500)


class AIAdviceRecommendation(_StrictSchemaModel):
    day: int | None = Field(default=None, ge=1, le=366)
    action: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


class TripAIAdvice(_StrictSchemaModel):
    summary: str = Field(min_length=1, max_length=2000)
    overall_score: int = Field(ge=1, le=10)
    strengths: list[AdviceListItem] = Field(
        default_factory=list,
        max_length=20,
    )
    issues: list[AIAdviceIssue] = Field(
        default_factory=list,
        max_length=20,
    )
    recommendations: list[AIAdviceRecommendation] = Field(
        default_factory=list,
        max_length=20,
    )
    weather_advice: list[AdviceListItem] = Field(
        default_factory=list,
        max_length=20,
    )
    packing_list: list[PackingListItem] = Field(
        default_factory=list,
        max_length=30,
    )
    disclaimer: str = Field(min_length=1, max_length=1000)
