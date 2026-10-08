from fastapi import APIRouter

from app.api.routes.documents import router as documents_router
from app.api.routes.grounded_ai import router as grounded_ai_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.rag import router as rag_router
from app.api.routes.security import router as security_router

api_router = APIRouter()
api_router.include_router(organizations_router)
api_router.include_router(documents_router)
api_router.include_router(rag_router)
api_router.include_router(grounded_ai_router)
api_router.include_router(security_router)

# api_router.include_router(health_router)
# api_router.include_router(users_router, prefix="/users", tags=["users"])