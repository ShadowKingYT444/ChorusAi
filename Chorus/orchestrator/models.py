from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PeerStatus(str, Enum):
    idle = "idle"
    busy = "busy"


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class PruneStatus(str, Enum):
    valid = "valid"
    suspect = "suspect"
    pruned = "pruned"


class PeerEntry(BaseModel):
    peer_id: str
    address: str | None = None
    model: str
    supported_models: list[str] = Field(default_factory=list)
    protocol_version: str = "1"
    joined_at: float
    last_seen: float
    status: PeerStatus = PeerStatus.idle
    pubkey: str | None = None
    verified: bool = False


class PeersResponse(BaseModel):
    count: int
    peers: list[PeerEntry]


class ClusterEntry(BaseModel):
    id: str
    label: str
    kind: str
    peer_ids: list[str]
    size: int


class ClusterEdge(BaseModel):
    source: str
    target: str
    weight: float
    kind: str


class ClusterStats(BaseModel):
    total_peers: int
    total_clusters: int
    total_edges: int
    total_jobs_observed: int


class ClustersResponse(BaseModel):
    clusters: list[ClusterEntry]
    edges: list[ClusterEdge]
    stats: ClusterStats


class BroadcastPlanRequest(BaseModel):
    prompt: str = Field(min_length=1)
    timeout_ms: int = Field(default=8000, ge=500, le=120_000)
    persona_catalog: list[str] | None = None
    target_peer_ids: list[str] | None = None


class BroadcastAssignment(BaseModel):
    peer_id: str
    persona_index: int
    persona: str


class BroadcastPlanResponse(BaseModel):
    job_id: str
    expected_peers: int
    timeout_ms: int
    target_peer_ids: list[str]
    assignments: list[BroadcastAssignment]


class RegisterMessage(BaseModel):
    type: Literal["register"]
    peer_id: str
    address: str | None = None
    model: str
    supported_models: list[str] | None = None
    protocol_version: str = "1"
    status: PeerStatus = PeerStatus.idle
    pubkey: str | None = None
    signed_ts: str | None = None
    ts: float | None = None


class SetStatusMessage(BaseModel):
    type: Literal["set_status"]
    status: PeerStatus


class SetAddressMessage(BaseModel):
    type: Literal["set_address"]
    address: str | None = None


class RelayMessage(BaseModel):
    type: Literal["relay"]
    to_peer_id: str
    payload: dict[str, Any]


class JoinRequestMessage(BaseModel):
    type: Literal["join_request"]
    peer_id: str
    address: str
    model: str
    supported_models: list[str] | None = None
    protocol_version: str = "1"


class JoinPeerRef(BaseModel):
    peer_id: str
    address: str | None = None
    model: str
    supported_models: list[str] = Field(default_factory=list)
    last_seen: float


class MeshConnectRequestMessage(BaseModel):
    type: Literal["mesh_connect_request"]
    to_peer_id: str
    peer_id: str
    address: str | None = None
    model: str
    supported_models: list[str] | None = None


class MeshConnectAcceptMessage(BaseModel):
    type: Literal["mesh_connect_accept"]
    to_peer_id: str
    peer_id: str


class HeartbeatMessage(BaseModel):
    type: Literal["heartbeat"]
    status: PeerStatus = PeerStatus.idle
    timestamp: float | None = None


class PeerGossipMessage(BaseModel):
    type: Literal["peer_gossip"]
    known_peers: list[JoinPeerRef]


class JobTarget(BaseModel):
    peer_id: str
    persona: str


class JobRequestMessage(BaseModel):
    type: Literal["job_request"]
    job_id: str
    prompt: str = Field(min_length=1)
    timeout_ms: int = Field(default=8000, ge=500, le=120_000)
    prompter_id: str
    peers: list[JobTarget]


class JobAckMessage(BaseModel):
    type: Literal["job_ack"]
    job_id: str
    peer_id: str
    prompter_id: str


class JobResponseMessage(BaseModel):
    type: Literal["job_response"]
    job_id: str
    peer_id: str
    prompter_id: str
    text: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    instance_id: str | None = None


class BroadcastEnvelopeMessage(BaseModel):
    type: Literal["broadcast_job"]
    prompt: str = Field(min_length=1)
    timeout_ms: int = Field(default=8000, ge=500, le=120_000)
    persona_catalog: list[str] | None = None
    target_peer_ids: list[str] | None = None
    job_id: str | None = None


class BroadcastInvokeRequest(BaseModel):
    job_id: str
    prompt: str = Field(min_length=1)
    timeout_ms: int = Field(default=8000, ge=500, le=120_000)
    persona_catalog: list[str] | None = None
    target_peer_ids: list[str] | None = None


class JobSpec(BaseModel):
    context: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    agent_count: int = Field(ge=1, le=10_000)
    rounds: int = Field(ge=1, le=128)
    payout: float = Field(ge=0.0)
    embedding_model_version: str | None = None
    review_mode: str | None = None
    template_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    completion_model: str | None = None


class CreateJobRequest(JobSpec):
    pass


class CreateJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    workspace_id: str
    shadow_credit_cost: int


class SlotRegistration(BaseModel):
    completion_base_url: str = Field(min_length=1)
    bearer_token: str | None = None
    external_participant_id: str | None = None
    model_id: str | None = None


class RegisterAgentsRequest(BaseModel):
    slots: dict[str, SlotRegistration] = Field(default_factory=dict)
    routing_mode: Literal["auto", "manual"] | None = None
    target_peer_ids: list[str] | None = None


class RegisterAgentsResponse(BaseModel):
    ok: bool
    registered_slots: list[str]


class CompletionResult(BaseModel):
    ok: bool
    text: str | None = None
    finish_reason: str | None = None
    error: str | None = None
    latency_ms: int | None = None


class SlotRuntime(BaseModel):
    slot_id: str
    registration: SlotRegistration
    status: PruneStatus = PruneStatus.valid
    bad_streak: int = 0
    c_impact: float = 0.0
    f_impact: float = 0.0
    last_context: str | None = None


class SlotRoundAudit(BaseModel):
    slot_id: str
    persona: str
    completion: str | None = None
    finish_reason: str | None = None
    embedding_id: str | None = None
    embedding: list[float] | None = None
    prune_status: PruneStatus
    watchdog_notes: list[str] = Field(default_factory=list)
    impact_c: float = 0.0
    impact_f: float = 0.0
    error: str | None = None


class RoundAudit(BaseModel):
    round: int
    slots: dict[str, SlotRoundAudit]
    nearest_edges: list[tuple[str, str]] = Field(default_factory=list)
    furthest_edges: list[tuple[str, str]] = Field(default_factory=list)


class JobRecord(BaseModel):
    job_id: str
    workspace_id: str
    spec: JobSpec
    status: JobStatus = JobStatus.pending
    slots: dict[str, SlotRuntime] = Field(default_factory=dict)
    rounds_data: list[RoundAudit] = Field(default_factory=list)
    current_round: int | None = None
    shadow_credit_cost: int = 0
    routing_mode: Literal["auto", "manual"] | None = None
    settlement_preview: dict[str, Any] | None = None
    final_answer: str | None = None
    citations: list[str] | None = None
    error: str | None = None


class JobPublicView(BaseModel):
    job_id: str
    workspace_id: str
    status: JobStatus
    current_round: int | None
    error: str | None
    shadow_credit_cost: int = 0
    routing_mode: Literal["auto", "manual"] | None = None
    settlement_preview: dict[str, Any] | None = None
    final_answer: str | None = None
    citations: list[str] | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    completion_model: str | None = None


class OperatorView(BaseModel):
    job_id: str
    workspace_id: str
    status: JobStatus
    current_round: int | None
    error: str | None
    shadow_credit_cost: int = 0
    routing_mode: Literal["auto", "manual"] | None = None
    rounds: list[RoundAudit]
    settlement_preview: dict[str, Any] | None = None
    final_answer: str | None = None
    citations: list[str] | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    completion_model: str | None = None


class AttachmentRecord(BaseModel):
    attachment_id: str
    workspace_id: str
    filename: str
    media_type: str
    kind: str
    size_bytes: int
    storage_path: str
    preview_text: str
    extracted_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class AttachmentUploadResponse(BaseModel):
    attachments: list[AttachmentRecord]


class AvailableModelEntry(BaseModel):
    model_id: str
    source: Literal["peer", "anchor"]
    route_count: int = 1
    peer_ids: list[str] = Field(default_factory=list)


class AvailableModelsResponse(BaseModel):
    models: list[AvailableModelEntry]
