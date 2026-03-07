"""Test ChromaDB persistence across restarts."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.knowledge_store import KnowledgeStore
from datetime import datetime


def test_persistence():
    """Test that data persists across KnowledgeStore instances."""
    
    # Create first instance and add a node
    store1 = KnowledgeStore(persist_directory="./test_db")
    
    test_node = {
        'concept': 'Test Concept',
        'summary': 'This is a test summary for persistence testing.',
        'source': 'test_source.txt',
        'node_type': 'raw',
        'confidence': 1.0,
        'contradiction_resolved': False,
        'connected_to': [],
        'relationship_types': [],
        'embedding': [0.1] * 1536,  # Dummy embedding
        'created_at': datetime.now().isoformat(),
        'times_retrieved': 0
    }
    
    node_id = store1.add_node(test_node)
    print(f"✓ Added node with ID: {node_id}")
    
    # Verify node exists
    retrieved = store1.get_node(node_id)
    assert retrieved is not None, "Node should exist in first instance"
    print(f"✓ Retrieved node from first instance: {retrieved['concept']}")
    
    # Create second instance (simulating restart)
    store2 = KnowledgeStore(persist_directory="./test_db")
    
    # Verify node still exists
    retrieved2 = store2.get_node(node_id)
    assert retrieved2 is not None, "Node should persist across instances"
    assert retrieved2['concept'] == 'Test Concept', "Node data should match"
    print(f"✓ Retrieved node from second instance: {retrieved2['concept']}")
    
    # Clean up
    store2.reset()
    print("✓ Cleaned up test database")
    
    print("\n✅ Persistence test passed!")


if __name__ == "__main__":
    test_persistence()
