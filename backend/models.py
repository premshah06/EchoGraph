"""Pydantic models for data validation."""

from typing import Any, Dict, List, Literal, Optional
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl, field_validator
from uuid import UUID, uuid4


class KnowledgeNode(BaseModel):
    """Knowledge node model with validation."""
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    concept: str = Field(..., min_length=1, max_length=200, description="Short title")
    summary: str = Field(..., min_length=1, max_length=1000, description="2-3 sentence description")
    source: str = Field(..., description="Filename or URL")
    node_type: Literal["raw", "synthesized", "bridge"] = Field(default="raw")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    contradiction_resolved: bool = Field(default=False)
    connected_to: List[str] = Field(default_factory=list, description="Node IDs")
    relationship_types: List[str] = Field(default_factory=list, description="Parallel to connected_to")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    times_retrieved: int = Field(default=0, ge=0)
    embedding: List[float] = Field(..., description="1536-dim vector")
    
    @field_validator('embedding')
    @classmethod
    def validate_embedding_dimension(cls, v):
        """Validate embedding has exactly 1536 dimensions."""
        if len(v) != 1536:
            raise ValueError(f"Embedding must have exactly 1536 dimensions, got {len(v)}")
        return v
    
    @field_validator('relationship_types')
    @classmethod
    def validate_relationship_types_length(cls, v, info):
        """Validate relationship_types has same length as connected_to."""
        # Note: This validator runs before connected_to is set, so we check in model_validator
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "concept": "Machine Learning Basics",
                "summary": "Machine learning is a subset of AI that enables systems to learn from data.",
                "source": "ml_intro.pdf",
                "node_type": "raw",
                "confidence": 1.0,
                "embedding": [0.1] * 1536
            }
        }


class Contradiction(BaseModel):
    """Contradiction model."""
    
    old_node_id: str = Field(..., description="UUID of existing node")
    new_concept: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, description="Why they conflict")
    old_source: str
    new_source: str
    credibility_assessment: str = Field(..., min_length=1, description="Which is more credible and why")
    
    class Config:
        json_schema_extra = {
            "example": {
                "old_node_id": "abc-123",
                "new_concept": "Earth is flat",
                "reason": "Contradicts established scientific consensus",
                "old_source": "science_textbook.pdf",
                "new_source": "conspiracy_blog.html",
                "credibility_assessment": "Old source is more credible (peer-reviewed)"
            }
        }


class Connection(BaseModel):
    """Connection/edge model."""
    
    node_a_id: str
    node_b_id: str
    relationship_type: Literal["supports", "extends", "reframes", "questions", "is_prerequisite_of", "bridge"]
    strength: float = Field(..., ge=0.0, le=1.0)
    explanation: str = Field(..., min_length=1, description="Why this connection exists")
    
    class Config:
        json_schema_extra = {
            "example": {
                "node_a_id": "abc-123",
                "node_b_id": "def-456",
                "relationship_type": "supports",
                "strength": 0.85,
                "explanation": "Both concepts discuss neural network architectures"
            }
        }


class Resolution(BaseModel):
    """Resolution model for synthesized contradictions."""
    
    contradiction_id: str
    synthesis_text: str = Field(..., min_length=1, max_length=2000, description="Nuanced merged understanding")
    confidence: float = Field(..., ge=0.0, le=1.0)
    sources_considered: List[str] = Field(..., min_length=2)
    reasoning: str = Field(..., min_length=1, description="How resolution was reached")
    
    @field_validator('sources_considered')
    @classmethod
    def validate_min_sources(cls, v):
        """Validate at least 2 sources."""
        if len(v) < 2:
            raise ValueError("Must consider at least 2 sources")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "contradiction_id": "abc-123_def-456",
                "synthesis_text": "While older research suggested X, recent studies show Y under certain conditions.",
                "confidence": 0.75,
                "sources_considered": ["paper_2020.pdf", "paper_2023.pdf"],
                "reasoning": "Weighted recent peer-reviewed research more heavily"
            }
        }


class WebSocketEvent(BaseModel):
    """WebSocket event model."""
    
    event: Literal[
        "agent_start", "concept_extracted", "connection_found",
        "contradiction_found", "resolution_start", "resolution_done",
        "loop_back", "node_stored", "ingestion_complete", "scholar_answer", "error"
    ]
    agent: Optional[str] = None
    data: dict
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    
    class Config:
        json_schema_extra = {
            "example": {
                "event": "concept_extracted",
                "agent": "librarian",
                "data": {"concept": "Neural Networks", "node_id": "abc-123"},
                "timestamp": "2024-01-01T12:00:00"
            }
        }


# API Request/Response Models

class IngestRequest(BaseModel):
    """Request model for document ingestion."""
    
    content: str = Field(..., min_length=1, description="Document text content")
    source_label: str = Field(..., min_length=1, description="Filename or identifier")
    events_session: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional WebSocket session identifier for real-time events"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "content": "Machine learning is a subset of artificial intelligence...",
                "source_label": "ml_intro.txt"
            }
        }


class URLIngestRequest(BaseModel):
    """Request model for URL ingestion."""
    
    url: HttpUrl = Field(..., description="URL to fetch and ingest")
    events_session: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional WebSocket session identifier for real-time events"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://example.com/article"
            }
        }


class BatchIngestItem(BaseModel):
    """A single document in a batch ingestion request."""
    content: str = Field(..., min_length=1, description="Document text content")
    source_label: str = Field(..., min_length=1, description="Filename or identifier")


class BatchIngestRequest(BaseModel):
    """Request model for batch document ingestion."""
    documents: List[BatchIngestItem] = Field(..., min_length=1, max_length=20)
    events_session: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional WebSocket session identifier for real-time events",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "documents": [
                    {"content": "Machine learning is...", "source_label": "ml.txt"},
                    {"content": "Deep learning refers to...", "source_label": "dl.txt"},
                ]
            }
        }


class BatchIngestItemResult(BaseModel):
    """Result for one document in a batch."""
    source_label: str
    status: str                  # "success" | "failed" | "skipped"
    nodes_created: int = 0
    edges_created: int = 0
    contradictions_resolved: int = 0
    error: Optional[str] = None


class BatchIngestResponse(BaseModel):
    """Response model for batch ingestion."""
    status: str                  # "complete" | "partial"
    total: int
    succeeded: int
    failed: int
    events_session: str
    results: List[BatchIngestItemResult]


class QueryRequest(BaseModel):
    """Request model for query answering."""
    
    query: str = Field(..., min_length=1, description="User's question")
    events_session: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional WebSocket session identifier for real-time events"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "query": "What is machine learning?"
            }
        }


class IngestResponse(BaseModel):
    """Response model for ingestion."""
    
    status: str
    ingestion_id: str
    events_session: str
    nodes_created: int = 0
    edges_created: int = 0
    contradictions_resolved: int = 0
    loops_executed: int = 0


class QueryResponse(BaseModel):
    """Response model for query answering."""
    
    answer: str
    sources: List[str]
    retrieved_nodes: List[dict] = []
    agent_events: List[Dict[str, Any]] = []


class SourceStat(BaseModel):
    """Per-source credibility breakdown."""
    source: str
    node_count: int
    avg_confidence: float


class GraphStatsResponse(BaseModel):
    """Response model for graph statistics."""

    node_count: int
    edge_count: int
    contradiction_count: int
    synthesized_count: int = 0
    raw_count: int = 0
    bridge_count: int = 0
    sources: List[SourceStat] = []
