"""Grounded AI generation API endpoints (Phase 3.3).

Implements grounding/anti-hallucination rules and prompt injection resistance.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.languages import resolve_conversation_language
from app.repositories.organizations import OrganizationRepository, get_repository
from app.services.grounded_ai import (
    GroundedAIRequest,
    GroundedAIResult,
    GroundedAIService,
    get_grounded_ai_service,
)
from app.services.security import PromptInjectionError

router = APIRouter(prefix="/grounded-ai", tags=["grounded-ai"])


class GroundedAIResponse(BaseModel):
    """API response model for grounded AI generation."""

    model_config = {"frozen": True}

    query: str
    response: str
    organization_id: str
    language: str
    sources: list[dict[str, int | None | str]]
    used_context: bool
    fallback: bool


def _to_response(result: GroundedAIResult) -> GroundedAIResponse:
    """Convert internal result to API response."""
    sources = []
    for doc_id, page in result.sources:
        source = {"document_id": doc_id}
        if page is not None:
            source["page_number"] = page
        sources.append(source)

    return GroundedAIResponse(
        query=result.query,
        response=result.response,
        organization_id=result.organization_id,
        language=result.language,
        sources=sources,
        used_context=result.used_context,
        fallback=result.fallback,
    )


@router.post("/generate", response_model=GroundedAIResponse)
async def generate_grounded_response(
    request: GroundedAIRequest,
    repository: Annotated[OrganizationRepository, Depends(get_repository)],
    grounded_ai_service: Annotated[GroundedAIService, Depends(get_grounded_ai_service)],
) -> GroundedAIResponse:
    """
    Generate a grounded AI response using RAG + generation.

    This endpoint implements Phase 3.3 Grounding/Anti-Hallucination:
    - Retrieves relevant knowledge using RAG
    - Sanitizes input for prompt injection
    - Generates response using AI with grounding instructions
    - Returns sources and fallback status

    Phase 3.6 Conversation Language: the language is resolved server-side
    from the organization record (default English); the request body cannot
    set or override it.
    """
    # Verify the user belongs to the requested organization
    await repository.membership(UUID(request.organization_id))

    # Resolve the conversation language from the organization record,
    # never from the request body or customer content.
    organization = await repository.get(UUID(request.organization_id))
    language = resolve_conversation_language(organization.default_language)

    # Generate the grounded response
    try:
        result = await grounded_ai_service.generate(
            query=request.query,
            organization_id=request.organization_id,
            conversation_context=request.conversation_context,
            language=language,
        )
    except PromptInjectionError:
        raise HTTPException(
            status_code=400,
            detail="This request cannot be processed for security reasons.",
        )
    except (ValueError, RuntimeError):
        raise HTTPException(
            status_code=503,
            detail="Grounded AI generation service unavailable.",
        )

    return _to_response(result)


@router.post("/check-injection", response_model=dict)
async def check_prompt_injection(
    request: GroundedAIRequest,
    repository: Annotated[OrganizationRepository, Depends(get_repository)],
    grounded_ai_service: Annotated[GroundedAIService, Depends(get_grounded_ai_service)],
) -> dict:
    """
    Check if a query contains prompt injection attempts.

    For testing and debugging prompt injection resistance.
    """
    # Verify the user belongs to the requested organization
    await repository.membership(UUID(request.organization_id))

    try:
        grounded_ai_service._sanitize_user_input(request.query)
        return {"safe": True, "message": "No prompt injection detected"}
    except PromptInjectionError:
        return {"safe": False, "message": "Prompt injection detected"}
