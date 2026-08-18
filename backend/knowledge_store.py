"""
Knowledge Store module for ChromaDB integration.
Handles persistent vector storage for knowledge nodes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


def hash_content(content: str) -> str:
    """Stable content hash used as the idempotency key for ingestion."""
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


class KnowledgeStore:
    """Persistent vector database for knowledge nodes with metadata."""

    COLLECTION_NAME = "knowledge_nodes"
    INGESTION_HASHES_COLLECTION_NAME = "ingestion_hashes"

    def __init__(self, persist_directory: str = "./echosystem_db"):
        """Initialize persistent ChromaDB client and collection."""
        self.persist_directory = persist_directory

        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )

        # Use cosine space so returned distance can be converted with similarity = 1 - distance.
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={
                "description": "GraphMediator AI knowledge graph nodes",
                "hnsw:space": "cosine",
            },
        )

        # Plain key-value lookup (content hash -> prior ingestion result) used
        # for idempotent ingestion. No embeddings needed, so it's cheap to
        # keep as a separate collection rather than overload the vector one.
        self.ingestion_hashes = self.client.get_or_create_collection(
            name=self.INGESTION_HASHES_COLLECTION_NAME,
            metadata={"description": "Content-hash -> prior ingestion result, for dedup"},
        )

    @staticmethod
    def _serialize_list(values: List[str]) -> str:
        return "||".join(v for v in values if v)

    @staticmethod
    def _deserialize_list(raw: Any) -> List[str]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(v) for v in raw if v]
        return [item for item in str(raw).split("||") if item]

    @staticmethod
    def _prepare_metadata(node: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "concept": str(node["concept"])[:200],
            "summary": str(node["summary"])[:1000],
            "source": str(node.get("source", "unknown"))[:1000],
            "node_type": str(node.get("node_type", "raw")),
            "confidence": float(node.get("confidence", 1.0)),
            "contradiction_resolved": bool(node.get("contradiction_resolved", False)),
            "connected_to": KnowledgeStore._serialize_list(
                [str(v) for v in node.get("connected_to", [])]
            ),
            "relationship_types": KnowledgeStore._serialize_list(
                [str(v) for v in node.get("relationship_types", [])]
            ),
            "edge_strengths": KnowledgeStore._serialize_list(
                [str(float(v)) for v in node.get("edge_strengths", [])]
            ),
            "times_retrieved": int(node.get("times_retrieved", 0)),
            "created_at": str(
                node.get("created_at")
                or datetime.now(timezone.utc).isoformat()
            ),
            "derivation": json.dumps(node["derivation"]) if node.get("derivation") else "",
        }

    @staticmethod
    def _normalize_node(node_id: str, metadata: Dict[str, Any], embedding: Optional[List[float]] = None) -> Dict[str, Any]:
        node = {
            "id": node_id,
            "concept": metadata.get("concept", ""),
            "summary": metadata.get("summary", ""),
            "source": metadata.get("source", ""),
            "node_type": metadata.get("node_type", "raw"),
            "confidence": float(metadata.get("confidence", 1.0)),
            "contradiction_resolved": bool(metadata.get("contradiction_resolved", False)),
            "connected_to": KnowledgeStore._deserialize_list(metadata.get("connected_to", "")),
            "relationship_types": KnowledgeStore._deserialize_list(metadata.get("relationship_types", "")),
            "edge_strengths": [
                float(v) for v in KnowledgeStore._deserialize_list(metadata.get("edge_strengths", ""))
                if v
            ],
            "times_retrieved": int(metadata.get("times_retrieved", 0)),
            "created_at": metadata.get("created_at", ""),
            "derivation": json.loads(metadata["derivation"]) if metadata.get("derivation") else None,
        }
        if embedding is not None:
            node["embedding"] = embedding
        return node

    def add_node(self, node: Dict[str, Any]) -> str:
        """
        Add a knowledge node with embedding and metadata.

        Returns the stored node ID.
        """
        try:
            if not node.get("concept") or not node.get("summary"):
                raise ValueError("Node must have non-empty concept and summary")

            embedding = node.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise ValueError("Node must include a non-empty embedding vector")

            node_id = str(node.get("id") or uuid4())
            if node_id.startswith("temp_"):
                node_id = str(uuid4())

            metadata = self._prepare_metadata(node)

            self.collection.add(
                ids=[node_id],
                embeddings=[embedding],
                metadatas=[metadata],
                documents=[metadata["summary"]],
            )

            logger.info("Added node %s (%s)", node_id, metadata["node_type"])
            return node_id
        except Exception:
            logger.exception("Error adding node to ChromaDB")
            raise

    def search_similar(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Semantic search for similar nodes."""
        try:
            if not query_embedding:
                return []

            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=max(1, top_k),
            )

            ids = results.get("ids", [[]])
            if not ids or not ids[0]:
                return []

            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            nodes: List[Dict[str, Any]] = []
            for idx, node_id in enumerate(ids[0]):
                metadata = metadatas[idx]
                distance = float(distances[idx]) if idx < len(distances) else 1.0

                # For cosine space in ChromaDB, distance is [0, 2], best is 0.
                similarity = max(0.0, min(1.0, 1.0 - distance))
                if similarity < threshold:
                    continue

                node = self._normalize_node(node_id=node_id, metadata=metadata)
                node["similarity"] = similarity
                nodes.append(node)

            return nodes
        except Exception:
            logger.exception("Error searching similar nodes")
            raise

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Retrieve all nodes for graph visualization."""
        try:
            results = self.collection.get(include=["metadatas"])
            ids = results.get("ids", [])
            metadatas = results.get("metadatas", [])

            nodes: List[Dict[str, Any]] = []
            for idx, node_id in enumerate(ids):
                metadata = metadatas[idx] if idx < len(metadatas) else {}
                nodes.append(self._normalize_node(node_id=node_id, metadata=metadata))

            return nodes
        except Exception:
            logger.exception("Error retrieving all nodes")
            raise

    def get_node(self, node_id: str, include_embedding: bool = False) -> Optional[Dict[str, Any]]:
        """Retrieve a specific node by ID."""
        try:
            include = ["metadatas"]
            if include_embedding:
                include.append("embeddings")

            results = self.collection.get(ids=[node_id], include=include)
            ids = results.get("ids", [])
            if not ids:
                return None

            metadata = results.get("metadatas", [{}])[0]
            embedding = None
            if include_embedding:
                embeddings = results.get("embeddings", [])
                if embeddings:
                    embedding = embeddings[0]

            return self._normalize_node(node_id=node_id, metadata=metadata, embedding=embedding)
        except Exception:
            logger.exception("Error retrieving node %s", node_id)
            return None

    def update_retrieval_count(self, node_id: str) -> None:
        """Increment times_retrieved counter for a node."""
        node = self.get_node(node_id=node_id)
        if not node:
            logger.warning("Cannot update retrieval count for unknown node: %s", node_id)
            return

        node["times_retrieved"] = int(node.get("times_retrieved", 0)) + 1
        metadata = self._prepare_metadata(node)

        self.collection.update(
            ids=[node_id],
            metadatas=[metadata],
        )

    def add_edge(self, connection: Dict[str, Any]) -> None:
        """Add a directed connection by updating source-node metadata."""
        node_a_id = str(connection.get("node_a_id", ""))
        node_b_id = str(connection.get("node_b_id", ""))
        relationship_type = str(connection.get("relationship_type", "related"))
        strength = float(connection.get("strength", 1.0))

        if not node_a_id or not node_b_id:
            logger.warning("Skipping edge with missing node IDs: %s", connection)
            return

        node_a = self.get_node(node_a_id)
        if not node_a:
            logger.warning("Source node not found for edge: %s", node_a_id)
            return

        connected_to = node_a.get("connected_to", [])
        relationship_types = node_a.get("relationship_types", [])
        edge_strengths = node_a.get("edge_strengths", [])

        if node_b_id in connected_to:
            return

        connected_to.append(node_b_id)
        relationship_types.append(relationship_type)
        edge_strengths.append(strength)
        node_a["connected_to"] = connected_to
        node_a["relationship_types"] = relationship_types
        node_a["edge_strengths"] = edge_strengths

        metadata = self._prepare_metadata(node_a)
        self.collection.update(
            ids=[node_a_id],
            metadatas=[metadata],
        )

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node and remove it from all neighbours' connection lists."""
        try:
            existing = self.get_node(node_id)
            if not existing:
                return False

            # Remove this node from any neighbour that points to it.
            all_nodes = self.get_all_nodes()
            for node in all_nodes:
                connected = node.get("connected_to", [])
                if node_id not in connected:
                    continue
                rel_types = node.get("relationship_types", [])
                strengths = node.get("edge_strengths", [])
                # Pad strengths to match length if older data missing it.
                while len(strengths) < len(connected):
                    strengths.append(1.0)
                triples = [
                    (t, r, s) for t, r, s in zip(connected, rel_types, strengths)
                    if t != node_id
                ]
                node["connected_to"] = [x[0] for x in triples]
                node["relationship_types"] = [x[1] for x in triples]
                node["edge_strengths"] = [x[2] for x in triples]
                self.collection.update(ids=[node["id"]], metadatas=[self._prepare_metadata(node)])

            self.collection.delete(ids=[node_id])
            return True
        except Exception:
            logger.exception("Error deleting node %s", node_id)
            return False


    def find_prior_ingestion(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Return the recorded result of a prior ingestion with this content hash, if any."""
        try:
            results = self.ingestion_hashes.get(ids=[content_hash], include=["metadatas"])
            metadatas = results.get("metadatas", [])
            if not metadatas:
                return None
            metadata = metadatas[0]
            return {
                "ingestion_id": metadata.get("ingestion_id", ""),
                "nodes_created": int(metadata.get("nodes_created", 0)),
                "edges_created": int(metadata.get("edges_created", 0)),
                "contradictions_resolved": int(metadata.get("contradictions_resolved", 0)),
                "loops_executed": int(metadata.get("loops_executed", 0)),
                "ingested_at": metadata.get("ingested_at", ""),
            }
        except Exception:
            logger.exception("Error checking prior ingestion for hash %s", content_hash)
            return None

    def record_ingestion(self, content_hash: str, result: Dict[str, Any]) -> None:
        """Record a completed ingestion's result under its content hash for future dedup checks."""
        try:
            metadata = {
                "ingestion_id": str(result.get("ingestion_id", "")),
                "nodes_created": int(result.get("nodes_created", 0)),
                "edges_created": int(result.get("edges_created", 0)),
                "contradictions_resolved": int(result.get("contradictions_resolved", 0)),
                "loops_executed": int(result.get("loops_executed", 0)),
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            self.ingestion_hashes.upsert(
                ids=[content_hash],
                documents=[content_hash],
                metadatas=[metadata],
            )
        except Exception:
            logger.exception("Error recording ingestion for hash %s", content_hash)

    def reset(self) -> None:
        """Wipe and recreate the knowledge collection and the ingestion-hash dedup table."""
        self.client.delete_collection(self.COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={
                "description": "GraphMediator AI knowledge graph nodes",
                "hnsw:space": "cosine",
            },
        )
        self.client.delete_collection(self.INGESTION_HASHES_COLLECTION_NAME)
        self.ingestion_hashes = self.client.get_or_create_collection(
            name=self.INGESTION_HASHES_COLLECTION_NAME,
            metadata={"description": "Content-hash -> prior ingestion result, for dedup"},
        )
