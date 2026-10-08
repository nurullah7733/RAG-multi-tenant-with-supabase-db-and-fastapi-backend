"""Security API endpoints (Phase 3.4).

Provides endpoints for testing and validating prompt injection resistance.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import AuthenticatedUser, get_current_user
from app.schemas.security import (
    ContentScanRequest,
    ContentScanResponse,
)
from app.services.security import (
    ContentSecurityService,
    PromptInjectionError,
    SensitiveDataError,
    get_security_service,
)

router = APIRouter(prefix="/security", tags=["security"])


@router.post("/scan", response_model=ContentScanResponse)
async def scan_content(
    request: ContentScanRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    security_service: Annotated[ContentSecurityService, Depends(get_security_service)],
) -> ContentScanResponse:
    """
    Scan content for security threats including prompt injection and sensitive data.
    
    This endpoint implements Phase 3.4 testing capabilities.
    """
    result = security_service.scan_for_injections(request.content)
    
    return ContentScanResponse(
        safe=result.safe,
        threats=list(result.threats),
        details=result.details,
    )


@router.post("/validate-input", response_model=dict)
async def validate_user_input(
    request: ContentScanRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    security_service: Annotated[ContentSecurityService, Depends(get_security_service)],
) -> dict:
    """
    Validate user input for prompt injection attempts.
    
    Returns success if input is safe, error if injection detected.
    """
    try:
        security_service.validate_user_input(request.content)
        return {"safe": True, "message": "Input is safe"}
    except (PromptInjectionError, ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Prompt injection attempt detected in input",
        )


@router.post("/validate-document", response_model=dict)
async def validate_document_content(
    request: ContentScanRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    security_service: Annotated[ContentSecurityService, Depends(get_security_service)],
) -> dict:
    """
    Validate document content for security threats.
    
    Returns success if document is safe, error if threats detected.
    """
    try:
        security_service.validate_document_content(request.content)
        return {"safe": True, "message": "Document is safe"}
    except PromptInjectionError:
        raise HTTPException(
            status_code=400,
            detail="Prompt injection attempt detected in document",
        )
    except (SensitiveDataError, ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Sensitive data exposure attempt detected in document",
        )
