"""Shared async helpers for API tests."""


async def upload(client, pdf: bytes, filename: str = "contract.pdf"):
    return await client.post("/api/contracts/upload", files={"file": (filename, pdf, "application/pdf")})


async def upload_and_process(client, pdf: bytes, filename: str = "contract.pdf") -> dict:
    """Upload, wait for the background job, return the full contract detail."""
    from app.services import job_runner
    r = await upload(client, pdf, filename)
    assert r.status_code == 202, r.text
    cid = r.json()["id"]
    await job_runner.wait_idle()
    detail = await client.get(f"/api/contracts/{cid}")
    assert detail.status_code == 200, detail.text
    return detail.json()


async def upload_version(client, contract_id, pdf: bytes, filename: str = "v2.pdf", **data) -> dict:
    """Upload a further version of an existing contract and wait for it to finish."""
    from app.services import job_runner
    r = await client.post(
        f"/api/contracts/{contract_id}/versions",
        files={"file": (filename, pdf, "application/pdf")},
        data={k: str(v) for k, v in data.items()},
    )
    assert r.status_code == 202, r.text
    await job_runner.wait_idle()
    return r.json()
