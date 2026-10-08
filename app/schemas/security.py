"""Pydantic schemas for Security Service (Phase 3.4)."""

from pydantic import BaseModel, Field


class ContentScanRequest(BaseModel):
    """Request model for content security scanning."""

    model_config = {"frozen": True, "extra": "forbid", "hide_input_in_errors": True}

    content: str = Field(
        min_length=1,
        max_length=50000,
        description="Content to scan for security threats",
    )


class ContentScanResponse(BaseModel):
    """Response model for content security scanning."""

    model_config = {"frozen": True}

    safe: bool = Field(description="Whether the content is safe")
    threats: list[str] = Field(
        default=[],
        description="List of detected threat types",
    )
    details: dict[str, str] = Field(
        default={},
        description="Additional details about detected threats",
    )
