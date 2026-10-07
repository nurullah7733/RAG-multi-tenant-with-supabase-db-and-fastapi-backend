from fastapi import APIRouter

from app.api.routes.documents import router as documents_router
from app.api.routes.organizations import router as organizations_router

api_router = APIRouter()
api_router.include_router(organizations_router)
api_router.include_router(documents_router)

# api_router.include_router(health_router)
# api_router.include_router(users_router, prefix="/users", tags=["users"])
