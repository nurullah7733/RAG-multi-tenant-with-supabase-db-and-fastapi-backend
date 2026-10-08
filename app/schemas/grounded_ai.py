"""Pydantic schemas for Grounded AI (Phase 3.3)."""

from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    """Reference to a source document and page."""

    model_config = {"frozen": True}

    document_id: str = Field(description="ID of the source document")
    page_number: int | None = Field(default=None, description="Page number if available")


class GroundedAIResponse(BaseModel):
    """API response model for grounded AI generation."""

    model_config = {"frozen": True}

    query: str = Field(description="The original query")
    response: str = Field(description="The AI-generated response")
    organization_id: str = Field(description="Organization ID for the response")
    sources: list[SourceReference] = Field(
        default=[], description="List of source document references"
    )
    used_context: bool = Field(
        default=False, description="Whether retrieved context was used"
    )
    fallback: bool = Field(
        default=False, description="Whether the fallback message was used"
    )


class GroundedAIRequest(BaseModel):
    """Request model for grounded AI generation."""

    model_config = {"frozen": True, "extra": "forbid", "hide_input_in_errors": True}

    query: str = Field(min_length=1, max_length=10000, description="User query")
    organization_id: str = Field(
        min_length=1, max_length=100, description="Organization ID"
    )
    conversation_context: str | None = Field(
        default=None,
        max_length=2000,
        description="Previous conversation context for context-aware generation",
    )
