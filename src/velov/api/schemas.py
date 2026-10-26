"""Contrat d'entrée / sortie de l'API (validé par Pydantic, publié dans OpenAPI)."""

from __future__ import annotations

from datetime import UTC

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class PredictionRequest(BaseModel):
    """Une observation de station à l'instant t (instant avec fuseau obligatoire)."""

    model_config = ConfigDict(extra="forbid")

    station_id: int = Field(..., ge=1, description="Identifiant de la station")
    timestamp: AwareDatetime = Field(..., description="Instant de l'observation, avec fuseau (normalisé en UTC)")
    capacity: int = Field(..., gt=0, le=100, description="Nombre total de places à la station")
    bikes_available: int = Field(..., ge=0, description="Nombre de vélos disponibles à la station")
    temperature: float = Field(..., ge=-30, le=50, description="Température en °C")
    is_raining: bool = Field(..., description="Pluie à l'instant de l'observation")

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def check_bikes_available(self) -> PredictionRequest:
        if self.bikes_available > self.capacity:
            raise ValueError("bikes_available ne peut pas dépasser capacity")
        return self


class PredictionResponse(BaseModel):
    station_id: int
    target_timestamp: AwareDatetime = Field(..., description="Instant prédit (t + 1 h), en UTC")
    predicted_bikes: float = Field(..., ge=0)
    model_version: str
