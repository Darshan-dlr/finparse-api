"""API v1 router — registers all endpoint modules."""
from fastapi import APIRouter
from app.api.v1.endpoints import documents

api_router = APIRouter()
api_router.include_router(documents.router, prefix="/documents", tags=["Financial Documents"])
