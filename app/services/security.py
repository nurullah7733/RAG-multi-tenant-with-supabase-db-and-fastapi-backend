"""Prompt Injection Resistance and Content Security Service (Phase 3.4).

Implements comprehensive prompt injection detection and content filtering
to protect against malicious input that could override system instructions
or reveal sensitive information.
"""

import logging
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.core.errors import AppError

logger = logging.getLogger(__name__)


class SecurityError(AppError):
    """Base security error."""

    status_code = 400
    code = "security_violation"
    message = "This request violates security policies."


class PromptInjectionError(SecurityError):
    """Prompt injection attempt detected."""

    code = "prompt_injection"
    message = "Prompt injection attempt detected and blocked."


class SensitiveDataError(SecurityError):
    """Sensitive data exposure attempt detected."""

    code = "sensitive_data_exposure"
    message = "Sensitive data exposure attempt detected and blocked."


@dataclass(frozen=True)
class SecurityScanResult:
    """Result of a security scan operation."""

    safe: bool
    threats: tuple[str, ...]
    details: dict[str, str]


class ContentSecurityService:
    """Service for detecting prompt injection and other security threats.
    
    This service implements Phase 3.4 requirements:
    - Distinguish between system instructions, app policy, retrieved content, user messages
    - Prevent override of tenant isolation or system rules
    - Never provide secrets, internal prompts, access tokens, or cross-tenant data
    """

    # Compiled patterns for efficiency (compiled once at class level)
    _INJECTION_PATTERNS: list[tuple[re.Pattern, str]]
    _SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]]
    _CROSS_TENANT_PATTERNS: list[tuple[re.Pattern, str]]

    def __init__(self) -> None:
        """Initialize with compiled regex patterns."""
        # Initialize injection patterns
        self._INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = self._compile_injection_patterns()
        self._SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = self._compile_sensitive_patterns()
        self._CROSS_TENANT_PATTERNS: list[tuple[re.Pattern, str]] = self._compile_cross_tenant_patterns()

    def _compile_injection_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Compile prompt injection detection patterns."""
        patterns = [
            # System override attempts
            (r"ignore\s+(all\s+)?previous\s+(instructions?|rules?|prompts?|messages?)", "system_override"),
            (r"forget\s+(all\s+)?previous\s+(instructions?|rules?|prompts?|messages?)", "system_override"),
            (r"disregard\s+(all\s+)?previous\s+(instructions?|rules?|prompts?|messages?)", "system_override"),
            (r"override\s+(all\s+)?previous\s+(instructions?|rules?|prompts?)", "system_override"),
            
            # Role manipulation
            (r"pretend\s+(you\s+are|to\s+be)", "role_manipulation"),
            (r"act\s+(as\s+if|like)", "role_manipulation"),
            (r"roleplay\s+(as|like)", "role_manipulation"),
            (r"you\s+are\s+(now|from\s+now\s+on)", "role_manipulation"),
            (r"from\s+now\s+on\s+,?\s*you\s+are", "role_manipulation"),
            
            # System prompt access attempts
            (r"system\s+(prompt|instruction|message|rule)", "system_prompt_access"),
            (r"show\s+(me\s+the\s+)?system\s+(prompt|instruction)", "system_prompt_access"),
            (r"reveal\s+(the\s+)?system\s+(prompt|instruction)", "system_prompt_access"),
            (r"display\s+(the\s+)?system\s+(prompt|instruction)", "system_prompt_access"),
            (r"output\s+(the\s+)?system\s+(prompt|instruction)", "system_prompt_access"),
            
            # Behavior change attempts
            (r"change\s+(your\s+)?behavior", "behavior_override"),
            (r"modify\s+(your\s+)?behavior", "behavior_override"),
            (r"adopt\s+(a\s+)?new\s+behavior", "behavior_override"),
            
            # Context manipulation
            (r"ignore\s+(the\s+)?context", "context_manipulation"),
            (r"forget\s+(the\s+)?context", "context_manipulation"),
            (r"disregard\s+(the\s+)?context", "context_manipulation"),
            
            # Direct instruction override
            (r"your\s+(true\s+)?instructions?\s+are", "instruction_override"),
            (r"your\s+(real\s+)?task\s+is", "instruction_override"),
            (r"your\s+(actual\s+)?purpose\s+is", "instruction_override"),
        ]
        
        return [(re.compile(pattern, re.IGNORECASE | re.MULTILINE), name) 
                for pattern, name in patterns]

    def _compile_sensitive_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Compile sensitive data exposure patterns."""
        patterns = [
            # API keys and tokens
            (r"api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_-]{20,}", "api_key_exposure"),
            (r"access[_-]?token\s*[:=]\s*['\"]?[A-Za-z0-9.-_]{20,}", "token_exposure"),
            (r"secret[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9.-_]{20,}", "secret_exposure"),
            (r"bearer\s+[A-Za-z0-9.-_]+", "bearer_token_exposure"),
            
            # Credentials
            (r"password\s*[:=]\s*['\"]?[^\s'\"]{6,}", "password_exposure"),
            (r"passphrase\s*[:=]\s*['\"]?[^\s'\"]{6,}", "password_exposure"),
            (r"username\s*[:=]\s*['\"]?[^\s'\"]+", "credential_exposure"),
            
            # Service role credentials (Supabase)
            (r"sb_[a-z0-9]+\.[a-z0-9.-]+", "service_role_exposure"),
            (r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", "jwt_exposure"),
        ]
        
        return [(re.compile(pattern, re.IGNORECASE), name) 
                for pattern, name in patterns]

    def _compile_cross_tenant_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Compile cross-tenant access patterns."""
        patterns = [
            # Attempts to access other organizations
            (r"other\s+(organizations?|tenants?|companies?|accounts?)\s+data", "cross_tenant_access"),
            (r"reveal\s+(other\s+)?(organizations?|tenants?|companies?)\s+data", "cross_tenant_access"),
            (r"show\s+(me\s+)?(other\s+)?(organizations?|tenants?)\s+information", "cross_tenant_access"),
            (r"access\s+(other\s+)?(organizations?|tenants?|companies?)", "cross_tenant_access"),
            (r"list\s+(all\s+)?(organizations?|tenants?|accounts?)", "cross_tenant_listing"),
            
            # Specific organization targeting
            (r"organization\s+[a-zA-Z0-9_-]+\s+data", "specific_org_access"),
            (r"tenant\s+[a-zA-Z0-9_-]+\s+information", "specific_org_access"),
        ]
        
        return [(re.compile(pattern, re.IGNORECASE | re.MULTILINE), name) 
                for pattern, name in patterns]

    def scan_for_injections(self, text: str) -> SecurityScanResult:
        """Scan text for prompt injection attempts.
        
        Args:
            text: The text to scan for injection attempts
            
        Returns:
            SecurityScanResult with safe=True/False and threat details
        """
        if not text or not isinstance(text, str):
            return SecurityScanResult(safe=True, threats=(), details={})
        
        threats: list[str] = []
        details: dict[str, str] = {}
        
        # Check injection patterns
        for pattern, threat_type in self._INJECTION_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                threats.append(threat_type)
                details[threat_type] = f"Found {len(matches)} matches"
        
        # Check sensitive data patterns
        for pattern, threat_type in self._SENSITIVE_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                threats.append(threat_type)
                details[threat_type] = f"Found {len(matches)} potential credential matches"
        
        # Check cross-tenant patterns
        for pattern, threat_type in self._CROSS_TENANT_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                threats.append(threat_type)
                details[threat_type] = f"Found {len(matches)} matches"
        
        return SecurityScanResult(
            safe=len(threats) == 0,
            threats=tuple(threats),
            details=details,
        )

    def validate_user_input(self, text: str) -> str:
        """Validate user input for prompt injection.
        
        Args:
            text: User-provided text to validate
            
        Returns:
            The validated text
            
        Raises:
            PromptInjectionError: If injection attempt detected
        """
        result = self.scan_for_injections(text)
        if not result.safe:
            logger.warning(
                "prompt_injection_detected: threats=%s, details=%s", 
                result.threats, result.details
            )
            raise PromptInjectionError()
        return text

    def validate_document_content(self, text: str) -> str:
        """Validate document content before storage/processing.
        
        Documents are untrusted input and must be scanned for:
        - Prompt injection attempts
        - Sensitive data exposure
        - Cross-tenant access attempts
        
        Args:
            text: Document text to validate
            
        Returns:
            The validated text
            
        Raises:
            PromptInjectionError: If prompt injection detected
            SensitiveDataError: If sensitive data exposure detected
        """
        result = self.scan_for_injections(text)
        
        if not result.safe:
            # For documents, we check for specific threat types
            has_injection = any(threat in ["system_override", "role_manipulation", 
                                         "system_prompt_access", "behavior_override", 
                                         "instruction_override"] 
                               for threat in result.threats)
            
            has_sensitive = any(threat in ["api_key_exposure", "token_exposure", 
                                          "secret_exposure", "password_exposure", 
                                          "credential_exposure", "jwt_exposure"] 
                               for threat in result.threats)
            
            has_cross_tenant = any(threat.startswith("cross_tenant") or 
                                  threat == "specific_org_access" 
                               for threat in result.threats)
            
            if has_injection:
                logger.warning(
                    "document_prompt_injection_detected: threats=%s", 
                    result.threats
                )
                raise PromptInjectionError()
            
            if has_sensitive:
                logger.warning(
                    "document_sensitive_data_detected: threats=%s", 
                    result.threats
                )
                raise SensitiveDataError()
            
            # Cross-tenant patterns in documents might be legitimate content
            # but we still want to flag them for review
            if has_cross_tenant:
                logger.info(
                    "document_cross_tenant_reference: threats=%s", 
                    result.threats
                )
        
        return text

    def validate_system_content(self, text: str) -> str:
        """Validate content that will be used as system instructions.
        
        This is the most restrictive check - system content should never
        contain user-controlled injection attempts.
        
        Args:
            text: System content to validate
            
        Returns:
            The validated text
            
        Raises:
            PromptInjectionError: If any injection pattern detected
        """
        result = self.scan_for_injections(text)
        if not result.safe:
            logger.error(
                "system_content_injection_detected: threats=%s, details=%s", 
                result.threats, result.details
            )
            raise PromptInjectionError()
        return text

    def sanitize_for_logging(self, text: str, max_length: int = 100) -> str:
        """Sanitize text for safe logging.
        
        Removes potentially sensitive information and truncates for safety.
        
        Args:
            text: Text to sanitize
            max_length: Maximum length for logging
            
        Returns:
            Sanitized text safe for logging
        """
        if not text:
            return ""
        
        # Remove sensitive patterns
        sanitized = text
        for pattern, _ in self._SENSITIVE_PATTERNS:
            sanitized = pattern.sub("[REDACTED]", sanitized)
        
        # Truncate
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "...[TRUNCATED]"
        
        return sanitized


# Singleton instance for dependency injection
_content_security_service: ContentSecurityService | None = None


def get_content_security_service() -> ContentSecurityService:
    """Get the singleton content security service."""
    global _content_security_service
    if _content_security_service is None:
        _content_security_service = ContentSecurityService()
    return _content_security_service


def get_security_service(
    service: Annotated[ContentSecurityService, Depends(get_content_security_service)],
) -> ContentSecurityService:
    """Dependency injection for FastAPI."""
    return service
