import logging

logger = logging.getLogger("homevision")

def evaluate_complexity(prompt: str) -> str:
    """
    Semantic Complexity Analyzer
    Evaluates whether a structural redesign requires High Complexity routing (Cloud LLM)
    or Low Complexity routing (Local CSP Matrix).
    """
    prompt_lower = prompt.lower()
    
    # Topological modification markers that warrant full spatial reasoning
    high_complexity_markers = [
        "open concept", "integrate", "flow", "duplex", "add room",
        "redesign", "restructure", "merge", "split", "rearrange",
        "make it a", "transform", "remodel"
    ]
    
    for marker in high_complexity_markers:
        if marker in prompt_lower:
            return "HIGH"
            
    # Simple direct dimension edits
    return "LOW"
