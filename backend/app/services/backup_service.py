"""
backup_service.py
-----------------
One-file backup and restore of everything a person owns: contracts, versions, the original files,
extracted values (with their status and any human review), obligations, deadlines, alerts, flags,
comparisons, summaries, chat history and policy rules.

The backup is a zip: data.json + files/<name>. It contains no password, session or key.
Restore ADDS the contracts as new records (new ids) to the current account; it never deletes or
overwrites anything, and a restored file is never trusted: names are validated, sizes are capped and
the zip is inspected before anything is read. Search chunks are rebuilt from the stored text.
"""

import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Tuple

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, Enum

from app.core.config import settings
from app.models.models import (
    Alert, ChatMessage, Clause, Contract, ContractParty, ContractVersion, Deadline, Document, ExtractedField,
    Obligation, ObligationOccurrence, PolicyRule, ReviewFlag, Summary, VersionChange,
)

FORMAT, FORMAT_VERSION = "contractlens-backup", 1
MODELS = [Contract, ContractVersion, Document, ExtractedField, ContractParty, Clause, Obligation, ObligationOccurrence,
          Deadline, Alert, ReviewFlag, VersionChange, Summary, ChatMessage]
MAX_ENTRIES = 20000
MAX_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_JSON = 300 * 1024 * 1024
_FILE_NAME = re.compile(r"^files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\.pdf|\.original\.docx)$")
_ORIGINAL = ".original.docx"


class BackupError(ValueError):
    """The file is not a usable backup. The message is safe to show."""


def _enc(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "value") and not isinstance(v, (str, int, float, bool)):
        return v.value
    return v


def _dec(col, v: Any) -> Any:
    if v is None:
        return None
    t = col.type
    if isinstance(t, PGUUID):
        return uuid.UUID(str(v))
    if isinstance(t, DateTime):
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    if isinstance(t, Date):
        return date.fromisoformat(v)
    if isinstance(t, Enum) and getattr(t, "enum_class", None):
        return t.enum_class(v)
    return v


def _columns(model):
    return list(sa_inspect(model).columns)


def _dump(model, row) -> Dict[str, Any]:
    return {c.key: _enc(getattr(row, c.key)) for c in _columns(model)}


async def build_backup(db: AsyncSession, user, storage_path) -> Tuple[bytes, Dict[str, int]]:
    contracts = list((await db.execute(select(Contract).where(Contract.user_id == user.id))).scalars())
    ids = [c.id for c in contracts]
    tables: Dict[str, List[Dict[str, Any]]] = {Contract.__tablename__: [_dump(Contract, c) for c in contracts]}
    counts = {"contracts": len(contracts)}
    documents: List[Document] = []
    for model in MODELS[1:]:
        if not ids:
            tables[model.__tablename__] = []
            continue
        rows = list((await db.execute(select(model).where(model.contract_id.in_(ids)))).scalars())
        tables[model.__tablename__] = [_dump(model, r) for r in rows]
        if model is Document:
            documents = rows
        counts[model.__tablename__] = len(rows)
    rules = list((await db.execute(select(PolicyRule).where(PolicyRule.user_id == user.id))).scalars())
    payload = {"format": FORMAT, "version": FORMAT_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(),
               "tables": tables, "policy_rules": [_dump(PolicyRule, r) for r in rules]}

    buf = io.BytesIO()
    files = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.json", json.dumps(payload, ensure_ascii=False))
        for d in documents:
            stem = d.storage_key.rsplit(".", 1)[0]
            for name in (d.storage_key, f"{stem}{_ORIGINAL}"):
                path = storage_path / name
                if _FILE_NAME.match(f"files/{name}") and path.exists():
                    z.write(path, f"files/{name}")
                    files += 1
    counts["files"], counts["policy_rules"] = files, len(rules)
    return buf.getvalue(), counts


def _inspect_zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("That is not a ContractLens backup (it could not be opened as a zip file).")
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise BackupError("The backup is larger than the allowed size.")
    names = {i.filename for i in infos}
    if "data.json" not in names:
        raise BackupError("That is not a ContractLens backup (data.json is missing).")
    for i in infos:
        if i.filename != "data.json" and not _FILE_NAME.match(i.filename):
            raise BackupError("The backup contains an unexpected file and was refused.")
    if z.getinfo("data.json").file_size > MAX_JSON:
        raise BackupError("The backup is larger than the allowed size.")
    return z


async def restore_backup(db: AsyncSession, user, data: bytes, storage_path, chunker) -> Dict[str, int]:
    z = _inspect_zip(data)
    try:
        payload = json.loads(z.read("data.json"))
    except ValueError:
        raise BackupError("The backup data could not be read.")
    if payload.get("format") != FORMAT or payload.get("version") != FORMAT_VERSION:
        raise BackupError("This backup was made by an incompatible version of ContractLens.")
    tables = payload.get("tables") or {}

    # Pass 1: every row gets a new id, so references can be rewritten regardless of order.
    new_id: Dict[str, uuid.UUID] = {}
    for model in MODELS:
        for r in tables.get(model.__tablename__, []):
            if isinstance(r, dict) and r.get("id"):
                new_id[str(r["id"])] = uuid.uuid4()

    def build(model, raw: Dict[str, Any]):
        vals = {}
        for col in _columns(model):
            if col.key not in raw:
                continue
            v = _dec(col, raw[col.key])
            if isinstance(v, uuid.UUID) and str(v) in new_id:
                v = new_id[str(v)]
            vals[col.key] = v
        vals["id"] = new_id[str(raw["id"])]
        if "user_id" in {c.key for c in _columns(model)}:
            vals["user_id"] = user.id
        return model(**vals)

    counts = {"contracts": 0, "versions": 0, "files": 0}
    pending_current: Dict[uuid.UUID, uuid.UUID] = {}
    file_map: Dict[str, str] = {}
    for raw in tables.get("documents", []):
        old_key = raw.get("storage_key", "")
        stem = old_key.rsplit(".", 1)[0]
        fresh = str(uuid.uuid4())
        file_map[old_key] = f"{fresh}.pdf"
        for src, dst in ((old_key, f"{fresh}.pdf"), (f"{stem}{_ORIGINAL}", f"{fresh}{_ORIGINAL}")):
            member = f"files/{src}"
            if member in z.namelist():
                (storage_path / dst).write_bytes(z.read(member))
                if dst.endswith(".pdf"):
                    counts["files"] += 1

    for model in MODELS:
        for raw in tables.get(model.__tablename__, []):
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            row = build(model, raw)
            if model is Contract:
                pending_current[row.id] = row.current_version_id
                row.current_version_id = None
                counts["contracts"] += 1
            if model is ContractVersion:
                counts["versions"] += 1
            if model is Document:
                row.storage_key = file_map.get(raw.get("storage_key", ""), row.storage_key)
            if model is ReviewFlag and row.target_id is not None and str(raw.get("target_id")) in new_id:
                row.target_id = new_id[str(raw["target_id"])]
            db.add(row)
        await db.flush()
    for cid, vid in pending_current.items():
        await db.execute(update(Contract).where(Contract.id == cid).values(current_version_id=vid))
    existing = {(r.name, r.field, r.operator, r.value, r.target) for r in (await db.execute(
        select(PolicyRule).where(PolicyRule.user_id == user.id))).scalars()}
    counts["policy_rules"] = 0
    for raw in payload.get("policy_rules") or []:
        if not (isinstance(raw, dict) and raw.get("name") and raw.get("field")):
            continue
        key = (raw.get("name"), raw.get("field"), raw.get("operator"), raw.get("value"), raw.get("target"))
        if key in existing or len(existing) >= 30:
            continue                                       # already there, or the per-user cap
        new_id.setdefault(str(raw.get("id") or uuid.uuid4()), uuid.uuid4())
        db.add(build(PolicyRule, {**raw, "id": raw.get("id") or str(uuid.uuid4())}))
        existing.add(key)
        counts["policy_rules"] += 1
    await db.flush()
    # Search chunks are derived data: rebuild them from the stored text.
    versions = (await db.execute(select(ContractVersion).where(ContractVersion.id.in_([new_id[str(r["id"])] for r in tables.get("contract_versions", []) if r.get("id")])))).scalars().all()
    for v in versions:
        if v.raw_text:
            await chunker(db, v)
    return counts
