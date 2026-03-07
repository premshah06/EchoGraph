"""LangGraph state definition for EchoGraph."""

from typing import Any, Callable, Dict, List, TypedDict


class EchoState(TypedDict):
    """
    Shared state structure for agent communication and control flow.
    Used by both ingestion and query graphs.
    """
    
    # Input
    input_type: str  # "document" | "query"
    raw_content: str  # Raw text of new document or user query
    source_label: str  # Filename or URL
    
    # Knowledge graph
    existing_nodes: List[Dict]  # Current KB nodes
    new_concepts: List[Dict]  # Extracted concepts
    contradictions: List[Dict]  # Detected contradictions
    connections: List[Dict]  # New relationships
    resolutions: List[Dict]  # Resolved contradictions
    
    # Query handling
    query_text: str  # User's question
    retrieved_nodes: List[Dict]  # Nodes for answering
    final_answer: str  # Scholar's answer
    
    # Control flow
    current_agent: str  # Active agent name
    processing_complete: bool  # Ingestion/query done
    contradiction_found: bool  # Critic found conflicts
    resolution_confidence: float  # 0.0-1.0 confidence
    loop_count: int  # Resolution loop counter
    
    # WebSocket session
    session_id: str  # WebSocket session ID for event streaming
    agent_events: List[Dict[str, Any]]  # Captured agent events for API response/debugging
    event_callback: Callable[[Dict[str, Any]], None]  # Optional callback for real-time event emission
