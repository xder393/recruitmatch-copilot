"""RecruitMatch v1 router composition."""
from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.matching import router as matching_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.operations import router as operations_router
from app.api.v1.resumes import router as resumes_router
from app.api.v1.evaluations import router as evaluations_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(jobs_router)
router.include_router(resumes_router)
router.include_router(knowledge_router)
router.include_router(matching_router)
router.include_router(operations_router)
router.include_router(evaluations_router)
