import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.database import engine, AsyncSessionLocal
from app.db.seed import seed_demo_user
from app.api import alerts, auth, chat, clauses, compare, contracts, data, deadlines, flags, obligations, pages, policy, portfolio, push, quality, renewals, review, search, summary, system
from app.services import alert_service, job_runner

logging.basicConfig(level=settings.LOG_LEVEL.upper())
logger = logging.getLogger("contractlens")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_startup()
    settings.absolute_storage_path
    if settings.AUTH_MODE in ("demo", "auto"):
        if settings.AUTH_MODE == "demo":
            logger.warning("AUTH_MODE=demo: all requests share ONE unauthenticated demo user. Local trial only.")
        async with AsyncSessionLocal() as session:
            await seed_demo_user(session)
    if settings.APP_ENV != "test":
        await job_runner.recover_incomplete()
        alert_service.start_scheduler()          # emails due alerts when SMTP is configured; harmless otherwise
    yield
    await alert_service.stop_scheduler()
    await job_runner.wait_idle()
    await engine.dispose()

app = FastAPI(
    title="ContractLens API",
    description="AI-assisted contract review with verifiable, source-linked outputs. Not legal advice.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, system.router, contracts.router, obligations.router, deadlines.router,
          chat.router, alerts.router, clauses.router, flags.router, compare.router, summary.router, pages.router, push.router, review.router, quality.router, search.router, renewals.router, portfolio.router, policy.router, data.router):
    app.include_router(r, prefix="/api")

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "app": "ContractLens API", "environment": settings.APP_ENV}
