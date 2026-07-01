"""
EchoGraph optimization config — Top Layer for the multi-agent KB pipeline.

Agent complexity assignments:
  librarian   → 0.35 (structured extraction, simple format)
  philosopher → 0.40 (relationship detection, binary + score)
  critic      → 0.65 (contradiction reasoning, needs nuance)
  synthesizer → 0.75 (resolution writing, quality matters)
  scholar     → 0.80 (user-facing answer, highest quality)

Field compression maps strip everything agents don't read from node payloads,
reducing per-call context by ~70–76%.
"""

from backend.optimization.configs.base import AgentConfig, OptimizationConfig

EchoGraphConfig = OptimizationConfig(
    default_model="gpt-4o-mini",
    max_tokens_per_call=1_600,
    cache_prefix_tokens=400,

    default_system_prompt=(
        "You are an expert knowledge engineer. "
        "Extract, analyze, and synthesize information with precision. "
        "Always respond in the exact format requested."
    ),

    agents={
        "librarian": AgentConfig(
            fields=[],          # librarian gets raw text, not node payloads — no compression
            model="gpt-4o-mini",
            base_complexity=0.35,
            temperature=0.3,
            system_prompt=(
                "You are a knowledge extraction expert. "
                "Extract distinct, self-contained knowledge concepts from documents. "
                "Always use the CONCEPT: / SUMMARY: format exactly as instructed."
            ),
        ),

        "philosopher": AgentConfig(
            # Only needs concept names to reason about relationships.
            fields=["id", "concept", "node_type"],
            model="gpt-4o-mini",
            base_complexity=0.40,
            temperature=0.3,
            system_prompt=(
                "You are a knowledge graph architect. "
                "Identify meaningful semantic relationships between concepts. "
                "Always respond with RELATIONSHIP: / STRENGTH: / EXPLANATION: format."
            ),
        ),

        "critic": AgentConfig(
            # Needs concept + summary + source to detect contradiction.
            fields=["id", "concept", "summary", "source", "confidence"],
            model="gpt-4o-mini",
            base_complexity=0.65,
            temperature=0.2,
            system_prompt=(
                "You are a critical reasoning expert. "
                "Identify factual contradictions between knowledge claims. "
                "Be conservative — only flag genuine factual conflicts, not differences in emphasis. "
                "Always respond with CONTRADICTION: / REASON: / CREDIBILITY: format."
            ),
        ),

        "synthesizer": AgentConfig(
            # Needs full claims to write a good resolution.
            fields=["id", "concept", "summary", "source", "confidence", "node_type"],
            model="gpt-4o",
            base_complexity=0.75,
            temperature=0.4,
            system_prompt=(
                "You are a knowledge synthesis expert. "
                "Resolve contradictions by producing a higher-confidence unified claim. "
                "Weigh source credibility, evidence quality, and logical consistency. "
                "Always respond with SYNTHESIS: / CONFIDENCE: / REASONING: format."
            ),
        ),

        "scholar": AgentConfig(
            # Scholar answers user questions — needs full context.
            fields=["id", "concept", "summary", "source", "confidence"],
            model="gpt-4o",
            base_complexity=0.80,
            temperature=0.4,
            system_prompt=(
                "You are a scholarly knowledge assistant. "
                "Answer questions using only the provided knowledge base nodes. "
                "Cite node IDs using [Node #id] format. "
                "Be precise, honest about uncertainty, and never hallucinate."
            ),
        ),
    },
)
