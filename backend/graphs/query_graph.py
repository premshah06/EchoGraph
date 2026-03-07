"""
Query Graph - LangGraph orchestration for question answering.
Implements the flow: START → Scholar → END
"""

import logging
from langgraph.graph import StateGraph, END
from backend.state import EchoState
from backend.agents.scholar import scholar_node
from backend.knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


def create_query_graph(knowledge_store: KnowledgeStore) -> StateGraph:
    """
    Create the query answering graph with Scholar agent.
    
    Args:
        knowledge_store: KnowledgeStore instance to pass to Scholar
    
    Returns:
        Compiled StateGraph ready for invocation
    
    Flow:
        START → Scholar → END
    
    Simple linear flow for question answering using semantic search.
    """
    logger.info("Creating query graph")
    
    # Initialize graph
    workflow = StateGraph(EchoState)
    
    # Create Scholar wrapper that passes knowledge_store
    def scholar_wrapper(state: EchoState) -> EchoState:
        return scholar_node(state, knowledge_store)
    
    # Add Scholar node
    workflow.add_node("scholar", scholar_wrapper)
    
    # Add edges
    workflow.set_entry_point("scholar")
    workflow.add_edge("scholar", END)
    
    # Compile graph
    app = workflow.compile()
    
    logger.info("Query graph compiled successfully")
    
    return app
