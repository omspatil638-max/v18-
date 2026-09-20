import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Environment must be set BEFORE any `app` import: tests never touch the real database. ──
BACKEND = Path(__file__).resolve().parent.parent
TEST_DB_URL = "postgresql+asyncpg://contractlens:contractlens@localhost:5432/contractlens_test"
STORAGE = Path(tempfile.mkdtemp(prefix="contractlens_test_storage_"))
os.environ.update({
    "DATABASE_URL": TEST_DB_URL,
    "APP_ENV": "test",
    "AUTH_MODE": "demo",
    "LLM_PROVIDER": "none",
    "LLM_MODEL": "",
    "GROQ_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "OPENAI_API_KEY": "",
    "STORAGE_PATH": str(STORAGE),
    "EMBEDDING_PROVIDER": "none",
    "ALERT_LEAD_DAYS": "30,14,7",
})
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402

from sample_data.generate import long_contract_text, write_blank_pdf, write_pdf  # noqa: E402
from tests.fake_llm import ContractReadingLLM  # noqa: E402


@pytest.fixture(scope="session")
def db_ready():
    assert "contractlens_test" in os.environ["DATABASE_URL"], "refusing to run tests against a non-test database"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True, env=os.environ)


@pytest_asyncio.fixture
async def client(db_ready):
    from app.db.database import engine
    from app.main import app
    from app.services import job_runner

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, contracts RESTART IDENTITY CASCADE"))
    for f in STORAGE.glob("*"):
        f.unlink()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await job_runner.wait_idle()


@pytest.fixture
def new_client():
    """Factory for extra clients with their own cookie jar (a second user)."""
    from app.main import app
    made = []

    def make():
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        made.append(c)
        return c

    yield make


@pytest.fixture
def llm(monkeypatch):
    """Enable the deterministic fake LLM for one test."""
    from app.core.config import settings
    from app.services.llm_service import set_fake_handler

    fake = ContractReadingLLM()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 10 ** 9)
    monkeypatch.setattr(settings, "LLM_RPM_LIMIT", 10 ** 6)
    set_fake_handler(fake)
    yield fake
    set_fake_handler(None)


@pytest.fixture(scope="session")
def long_pdf(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("samples") / "long.pdf"
    write_pdf(p, long_contract_text())
    return p.read_bytes()


@pytest.fixture(scope="session")
def blank_pdf(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("samples") / "blank.pdf"
    write_blank_pdf(p)
    return p.read_bytes()


@pytest.fixture(scope="session")
def tiny_pdf(tmp_path_factory) -> bytes:
    """A real PDF with text but none of the facts we extract."""
    p = tmp_path_factory.mktemp("samples") / "tiny.pdf"
    write_pdf(p, "MEMORANDUM\nThis is a short internal note about the office picnic. Please bring your own chairs.\n"
                 "It contains no contractual terms whatsoever, only logistics for the day.\n")
    return p.read_bytes()
