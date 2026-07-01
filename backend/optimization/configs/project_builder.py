"""
ProjectBuilder optimization config — Top Layer for the code-generation assistant.

Use case: an AI system that helps build UI + backend projects.
Agents work as a pipeline:
  planner     → breaks task into steps (needs full context, deep reasoning)
  coder       → implements each step (needs plan + file context)
  reviewer    → checks code quality (lighter model, structured checklist)
  documenter  → writes comments/docs (simplest task, nano model)

Field compression maps for code context payloads:
  A "code context" payload might carry:
    { task, stack, file_path, existing_code, plan, requirements, language }
  Each agent only gets the fields it actually reads.
"""

from backend.optimization.configs.base import AgentConfig, OptimizationConfig

ProjectBuilderConfig = OptimizationConfig(
    default_model="gpt-4o-mini",
    max_tokens_per_call=3_200,    # code needs more room than KB nodes
    cache_prefix_tokens=500,

    default_system_prompt=(
        "You are an expert software engineer. "
        "Write clean, production-quality code. "
        "Follow the exact output format specified in each instruction."
    ),

    agents={
        "planner": AgentConfig(
            # Planner needs the full task description and technology stack.
            fields=["task", "stack", "requirements", "existing_files"],
            model="gpt-4o",
            base_complexity=0.80,
            temperature=0.3,
            system_prompt=(
                "You are a senior software architect. "
                "Break complex engineering tasks into clear, ordered implementation steps. "
                "Each step must be actionable and self-contained. "
                "Output a numbered JSON array of steps with: step, file, action, rationale."
            ),
        ),

        "coder": AgentConfig(
            # Coder needs the plan and the specific file's existing content.
            fields=["plan", "file_path", "existing_code", "language", "stack"],
            model="gpt-4o",
            base_complexity=0.75,
            temperature=0.25,
            system_prompt=(
                "You are an expert software engineer. "
                "Implement the requested changes precisely. "
                "Output only the complete updated file content — no prose, no markdown fences. "
                "Preserve all existing functionality unless explicitly told to change it."
            ),
        ),

        "reviewer": AgentConfig(
            # Reviewer only needs code + requirements — not the full plan.
            fields=["code", "requirements", "language"],
            model="gpt-4o-mini",
            base_complexity=0.55,
            temperature=0.2,
            system_prompt=(
                "You are a code reviewer focused on correctness, security, and maintainability. "
                "Output a JSON object with: "
                "approved (bool), issues (list of strings), suggestions (list of strings). "
                "Be concise. Flag real problems, not style preferences."
            ),
        ),

        "documenter": AgentConfig(
            # Documenter just adds comments — simplest task, cheapest model.
            fields=["code", "language"],
            model=None,          # let router pick — will get nano/mini
            base_complexity=0.25,
            temperature=0.2,
            system_prompt=(
                "You are a technical writer. "
                "Add clear, concise inline comments to the provided code. "
                "Document the WHY not the WHAT. "
                "Return only the commented code — no prose."
            ),
        ),
    },
)
