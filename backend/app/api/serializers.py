"""
serializers.py — build API responses from the version-scoped model.

The current version's processing state and key terms are flattened onto the contract
response, so a client can keep treating "a contract" as one thing, while `versions`
and `contract_version_id` expose exactly which revision every value came from.
"""

import uuid
from typing import Dict, Iterable, List, Optional

from app.models.models import (
    Alert, Contract, ContractVersion, Document, ExtractionStatus,
)
from app.schemas.schemas import (
    AlertResponse, ContractDetail, ContractStatusResponse, ContractSummary, VersionSummary,
)


def main_filename(documents: Iterable[Document], version_id: Optional[uuid.UUID]) -> str:
    """Display name of the file backing a version."""
    docs = list(documents)
    for d in docs:
        if d.version_id == version_id:
            return d.filename
    return docs[0].filename if docs else "unknown.pdf"


def version_summary(version: ContractVersion, filename: Optional[str] = None) -> VersionSummary:
    return VersionSummary(
        id=version.id, contract_id=version.contract_id, version_number=version.version_number,
        label=version.label, status=version.status, stage=version.stage, progress=version.progress,
        extraction_status=version.extraction_status, extraction_error=version.extraction_error,
        effective_date=version.effective_date, expiry_date=version.expiry_date,
        page_count=version.page_count, created_at=version.created_at,
        processed_at=version.processed_at, filename=filename,
    )


SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def flag_sort_key(flag) -> tuple:
    return (SEVERITY_RANK.get(flag.severity.value, 9), -(flag.created_at.timestamp() if flag.created_at else 0))


def open_flag_counts(flags) -> tuple:
    open_ = [f for f in flags if f.status.value == "OPEN"]
    return len(open_), sum(1 for f in open_ if f.severity.value == "HIGH")


def _base(contract: Contract, version: ContractVersion, documents: List[Document], version_count: int,
          flag_counts: tuple = (0, 0)) -> Dict:
    return {
        "id": contract.id,
        "title": contract.title,
        "counterparty": contract.counterparty,
        "contract_type": contract.contract_type,
        "tags": list(contract.tags or []),
        "filename": main_filename(documents, version.id),
        "current_version_id": contract.current_version_id or version.id,
        "version_count": version_count,
        "open_flag_count": flag_counts[0],
        "high_flag_count": flag_counts[1],
        "status": version.status,
        "stage": version.stage,
        "progress": version.progress,
        "extraction_status": version.extraction_status,
        "extraction_error": version.extraction_error,
        "effective_date": version.effective_date,
        "expiry_date": version.expiry_date,
        "renewal_terms": version.renewal_terms,
        "payment_terms": version.payment_terms,
        "termination_conditions": version.termination_conditions,
        "page_count": version.page_count,
        "created_at": contract.created_at,
        "processed_at": version.processed_at,
        "deleted_at": contract.deleted_at,
    }


def contract_summary(contract: Contract, version: ContractVersion, documents: List[Document],
                     parties: List, version_count: int = 1, flag_counts: tuple = (0, 0)) -> ContractSummary:
    return ContractSummary(**_base(contract, version, documents, version_count, flag_counts), parties=parties)


def contract_detail(contract: Contract, version: ContractVersion, documents: List[Document],
                    versions: List[ContractVersion]) -> ContractDetail:
    by_version_file = {v.id: main_filename(documents, v.id) for v in versions}
    return ContractDetail(
        **_base(contract, version, documents, len(versions), open_flag_counts(version.flags)),
        extraction_meta=version.extraction_meta,
        versions=[version_summary(v, by_version_file.get(v.id)) for v in versions],
        documents=documents,
        parties=sorted(version.parties, key=lambda p: (p.source_page or 10 ** 6, p.name)),
        fields=sorted(version.fields, key=lambda f: (f.field_key, f.item_index)),
        clauses=sorted(version.clauses, key=lambda c: (c.source_page or 10 ** 6, c.title)),
        obligations=sorted(version.obligations, key=lambda o: (o.due_date is None, o.due_date, o.source_page or 10 ** 6)),
        deadlines=sorted(version.deadlines, key=lambda d: d.deadline_date),
        flags=sorted(version.flags, key=flag_sort_key),
    )


def contract_status(contract: Contract, version: ContractVersion) -> ContractStatusResponse:
    return ContractStatusResponse(
        id=contract.id, current_version_id=contract.current_version_id or version.id,
        status=version.status, stage=version.stage, progress=version.progress,
        extraction_status=version.extraction_status, extraction_error=version.extraction_error,
    )


def alert_response(alert: Alert, contract_title: str, today) -> AlertResponse:
    deadline = alert.deadline
    return AlertResponse(
        id=alert.id, contract_id=alert.contract_id, deadline_id=alert.deadline_id,
        contract_title=contract_title, lead_days=alert.lead_days, fire_on=alert.fire_on,
        status=alert.status, message=alert.message, deadline_type=deadline.deadline_type,
        deadline_date=deadline.deadline_date, days_until=(deadline.deadline_date - today).days,
        remind_at=alert.fire_on, acknowledged=alert.acknowledged,
        email_count=alert.email_count or 0, emailed_at=alert.emailed_at,
    )


def is_processing(version: ContractVersion) -> bool:
    return version.extraction_status in (ExtractionStatus.PENDING.value, ExtractionStatus.RUNNING.value)
