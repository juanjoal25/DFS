"""Modelos de request/response compartidos (Pydantic)."""
from typing import List
from pydantic import BaseModel


# --- Auth ---
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int


# --- Upload plan ---
class UploadPlanRequest(BaseModel):
    path: str
    size: int
    block_ids: List[str]  # en orden de secuencia (seq = índice)


class BlockPlan(BaseModel):
    block_id: str
    seq: int
    datanode_urls: List[str]  # [primario, replica, ...]


class UploadPlanResponse(BaseModel):
    file_id: str
    block_size: int
    blocks: List[BlockPlan]


class CommitRequest(BaseModel):
    file_id: str


class CommitResponse(BaseModel):
    committed: bool


# --- Descarga ---
class BlockLocation(BaseModel):
    block_id: str
    seq: int
    datanode_urls: List[str]


class FileMapResponse(BaseModel):
    file_id: str
    path: str
    size: int
    block_size: int
    blocks: List[BlockLocation]


# --- Namespace ---
class LsEntry(BaseModel):
    name: str
    type: str  # "file" | "dir"
    size: int


class LsResponse(BaseModel):
    entries: List[LsEntry]


class MkdirRequest(BaseModel):
    path: str


class GenericResult(BaseModel):
    ok: bool
    detail: str = ""


# --- Heartbeat ---
class HeartbeatPayload(BaseModel):
    datanode_id: str
    url: str
    free_space_bytes: int
    block_ids: List[str]
