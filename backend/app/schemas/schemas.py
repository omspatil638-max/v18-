import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.models import (
    AlertStatus, ChangeType, ClauseType, ContractStatus, DeadlineType, DocumentKind,
    FlagSeverity, FlagStatus, MessageRole, ObligationStatus,
)

# ─── User / auth ───────────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    phone: Optional[str] = None
    email_verified: bool = False
    onboarded: bool = True
    model_config = ConfigDict(from_attributes=True)

class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=256)

class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)

class AuthConfigResponse(BaseModel):
    mode: str                       # demo | setup (nobody has signed up yet) | login
    registration_open: bool
    email_verification_available: bool = False

# ─── Shared-shape extracted items ──────────────────────────────────────────────
# value · source_quote · page · section · confidence (0-1) · status · contract_version_id

class ExtractedFieldResponse(BaseModel):
    id: uuid.UUID
    contract_version_id: uuid.UUID
    field_key: str
    item_index: int = 0
    value: Optional[Dict[str, Any]] = None
    display_value: Optional[str] = None
    source_quote: Optional[str] = None
    page: Optional[int] = None
    section: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    notes: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(from_attributes=True)

class ContractPartyResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version_id: uuid.UUID
    role: str
    name: str
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    model_config = ConfigDict(from_attributes=True)

class ClauseResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version_id: uuid.UUID
    clause_type: ClauseType
    title: str
    content: str
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    model_config = ConfigDict(from_attributes=True)

class ObligationUpdate(BaseModel):
    status: Optional[ObligationStatus] = None

class ObligationResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version_id: uuid.UUID
    responsible_party: str
    action: str
    due_rule: str
    due_rule_type: Optional[str] = None
    due_date: Optional[date] = None
    due_computed: bool = False               # True when the date was worked out in code from a rule
    due_basis: Optional[str] = None          # how it was worked out, e.g. "30 days after the effective date (2026-01-15)"
    due_reason: Optional[str] = None         # why NO date exists (rule depends on an event the contract does not date)
    is_overdue: bool = False
    recurrence: Optional[str] = None
    status: ObligationStatus                 # lifecycle: pending / completed / overdue
    verification_status: str                 # shared extraction status
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)

class DeadlineResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version_id: uuid.UUID
    obligation_id: Optional[uuid.UUID] = None
    label: str
    deadline_date: date
    deadline_type: DeadlineType
    created_at: datetime
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None
    basis: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class DeadlineWithContext(DeadlineResponse):
    """A deadline as the dashboard shows it: which contract, how far away, overdue or not."""
    contract_title: str
    days_until: int
    is_overdue: bool = False
    responsible_party: Optional[str] = None
    obligation_status: Optional[ObligationStatus] = None
    verification_status: Optional[str] = None

class ScheduleEntry(BaseModel):
    """One dated item on a contract's obligation timeline."""
    id: str
    kind: str                                # contract_date | renewal_notice | obligation | occurrence
    due_on: date
    label: str
    party: Optional[str] = None              # who is responsible; None for contract-level dates
    action: Optional[str] = None
    rule_type: Optional[str] = None          # fixed | relative | recurring | none
    recurrence: Optional[str] = None
    basis: Optional[str] = None
    is_overdue: bool = False
    status: Optional[str] = None             # obligation lifecycle
    verification_status: Optional[str] = None
    obligation_id: Optional[uuid.UUID] = None
    deadline_id: Optional[uuid.UUID] = None
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None

class UndatedObligation(BaseModel):
    """An obligation with a timing rule that could not be turned into a date. Shown, never hidden."""
    obligation_id: uuid.UUID
    party: str
    action: str
    due_rule: str
    reason: Optional[str] = None
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None
    verification_status: Optional[str] = None

class ScheduleResponse(BaseModel):
    contract_version_id: uuid.UUID
    entries: List[ScheduleEntry] = []
    undated: List[UndatedObligation] = []

class ReviewFlagResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version_id: uuid.UUID
    code: str
    severity: FlagSeverity
    reason: str
    target_type: Optional[str] = None
    target_id: Optional[uuid.UUID] = None
    target_label: Optional[str] = None
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_quote: Optional[str] = None
    status: FlagStatus
    created_at: datetime
    resolved_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class ReviewFlagUpdate(BaseModel):
    status: FlagStatus

class QueueFlag(ReviewFlagResponse):
    """A flag in the cross-contract 'Needs review' queue."""
    contract_title: str
    version_label: str
    resolved_at: Optional[datetime] = None

class FlagSummary(BaseModel):
    open: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0

# ─── Alerts ────────────────────────────────────────────────────────────────────

class AlertResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    deadline_id: uuid.UUID
    contract_title: str
    lead_days: int
    fire_on: date
    status: AlertStatus
    message: str
    deadline_type: DeadlineType
    deadline_date: date
    days_until: int
    # Backwards-compatible aliases used by the current dashboard.
    remind_at: date
    acknowledged: bool
    email_count: int = 0
    emailed_at: Optional[datetime] = None

# ─── Documents and versions ────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    version_id: Optional[uuid.UUID] = None
    kind: DocumentKind
    filename: str
    page_count: int = 0
    size_bytes: int = 0
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class VersionSummary(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    version_number: int
    label: str
    status: ContractStatus
    stage: Optional[str] = None
    progress: int = 0
    extraction_status: str
    extraction_error: Optional[str] = None
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    page_count: int = 0
    created_at: datetime
    processed_at: Optional[datetime] = None
    filename: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

# ─── Contract ──────────────────────────────────────────────────────────────────
# The current version's state is flattened onto the contract so existing clients
# keep working; `versions` exposes the full history.
# storage keys and raw text are never returned here.

class ContractSummary(BaseModel):
    id: uuid.UUID
    title: str
    counterparty: Optional[str] = None
    contract_type: Optional[str] = None
    tags: List[str] = []
    filename: str
    current_version_id: Optional[uuid.UUID] = None
    version_count: int = 1
    open_flag_count: int = 0
    high_flag_count: int = 0
    status: ContractStatus
    stage: Optional[str] = None
    progress: int = 0
    extraction_status: str
    extraction_error: Optional[str] = None
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    renewal_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    termination_conditions: Optional[str] = None
    page_count: int = 0
    created_at: datetime
    processed_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    parties: List[ContractPartyResponse] = []

class ContractDetail(ContractSummary):
    extraction_meta: Optional[Dict[str, Any]] = None
    versions: List[VersionSummary] = []
    documents: List[DocumentResponse] = []
    fields: List[ExtractedFieldResponse] = []
    clauses: List[ClauseResponse] = []
    obligations: List[ObligationResponse] = []
    deadlines: List[DeadlineResponse] = []
    flags: List[ReviewFlagResponse] = []

class ContractStatusResponse(BaseModel):
    id: uuid.UUID
    current_version_id: Optional[uuid.UUID] = None
    status: ContractStatus
    stage: Optional[str] = None
    progress: int = 0
    extraction_status: str
    extraction_error: Optional[str] = None

class ContractUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    counterparty: Optional[str] = Field(default=None, max_length=255)
    contract_type: Optional[str] = Field(default=None, max_length=40)
    tags: Optional[List[str]] = Field(default=None, max_length=10)

class PageText(BaseModel):
    page: int
    text: str

class ContractText(BaseModel):
    contract_version_id: uuid.UUID
    page_count: int
    pages: List[PageText]

# ─── Version comparison (Phase 6) ──────────────────────────────────────────────

class VersionChangeResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    from_version_id: uuid.UUID
    to_version_id: uuid.UUID
    change_type: ChangeType
    category: str
    label: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_quote: Optional[str] = None
    new_quote: Optional[str] = None
    old_page: Optional[int] = None
    new_page: Optional[int] = None
    old_section: Optional[str] = None
    new_section: Optional[str] = None
    is_material: bool = False
    impact_text: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

# ─── System ────────────────────────────────────────────────────────────────────

class SystemStatus(BaseModel):
    llm_provider: str
    llm_model: str
    llm_configured: bool
    llm_reason: Optional[str] = None
    embedding_provider: str = "none"
    embedding_model: str = ""
    embedding_available: bool = False
    privacy_redaction: bool = False
    auth_mode: str
    max_upload_mb: int
    disclaimer: str

# ─── Chat ──────────────────────────────────────────────────────────────────────

class CitationSchema(BaseModel):
    chunk_id: Optional[str] = None
    source_page: int
    source_section: str
    snippet: str
    verified: bool = True

class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_version_id: Optional[uuid.UUID] = None
    role: MessageRole
    content: str
    citations: Optional[List[CitationSchema]] = None
    answer_status: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# ─── Version comparison ────────────────────────────────────────────────────────

class ComparisonSummary(BaseModel):
    added: int = 0
    removed: int = 0
    modified: int = 0
    unchanged: int = 0
    material: int = 0          # material changes (key terms and key clauses)
    key_changes: int = 0       # material changes to the key terms (dates, notice, amounts, renewal, parties)

class ComparisonResponse(BaseModel):
    contract_id: uuid.UUID
    from_version: VersionSummary
    to_version: VersionSummary
    status: str                # ready | running | not_run | versions_not_ready
    impact_status: str = "none"   # complete | partial | unavailable | none
    notes: List[str] = []
    summary: ComparisonSummary = ComparisonSummary()
    changes: List[VersionChangeResponse] = []

class DiffSegment(BaseModel):
    op: str                    # equal | delete | insert
    text: str

class AlignedSide(BaseModel):
    label: str
    page: Optional[int] = None
    text: str

class AlignedSection(BaseModel):
    change_type: ChangeType
    old: Optional[AlignedSide] = None
    new: Optional[AlignedSide] = None
    similarity: float = 0.0
    diff: List[DiffSegment] = []
    is_material: bool = False

class AlignedSections(BaseModel):
    from_version_id: uuid.UUID
    to_version_id: uuid.UUID
    sections: List[AlignedSection] = []
