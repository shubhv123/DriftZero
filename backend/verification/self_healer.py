# Parses stderr from failed jobs and loops back to Nemotron Ultra
import ast
import re
from pathlib import Path
from typing import List, Optional, Tuple

from backend.config import NEBIUS_API_KEY, NEBIUS_BASE_URL, NEMOTRON_ULTRA_MODEL
from backend.intelligence.prompts import SELF_HEAL_SYSTEM_PROMPT
from backend.intelligence.token_factory_router import (
    _extract_code_block,
    _get_node_start_lineno,
    _is_valid_complete_replacement,
    _splice_ast_patch,
)


def heal_failed_file_with_ultra(
    patched_repo_dir: Path,
    rel_file_path: str,
    pytest_traceback: str,
) -> Tuple[bool, str]:
    """
    Sends the failing file + pytest traceback to Nemotron 3 Ultra on Nebius Token Factory
    to synthesize a self-healed patch. Works universally on any file from any repository.
    """
    target_file = patched_repo_dir / rel_file_path
    if not target_file.exists():
        return False, NEMOTRON_ULTRA_MODEL

    broken_code = target_file.read_text(encoding="utf-8")

    if not NEBIUS_API_KEY:
        print(f"  ❌ [Self-Heal Error] NEBIUS_API_KEY is not set. Cannot run autonomous self-healing for {rel_file_path}.")
        return False, NEMOTRON_ULTRA_MODEL

    try:
        from openai import OpenAI

        client = OpenAI(api_key=NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL, timeout=60.0, max_retries=1)
        user_prompt = (
            f"Broken File ({rel_file_path}):\n```python\n{broken_code}\n```\n\n"
            f"Pytest Failure Traceback from Verification Job:\n```\n{pytest_traceback}\n```"
        )
        res = client.chat.completions.create(
            model=NEMOTRON_ULTRA_MODEL,
            messages=[
                {"role": "system", "content": SELF_HEAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.05,
            max_tokens=8192,
        )
        raw_text = res.choices[0].message.content or ""
        if not raw_text.strip() and hasattr(res.choices[0].message, "model_extra") and isinstance(res.choices[0].message.model_extra, dict):
            for k in ("reasoning_content", "reasoning", "thought", "thinking"):
                if res.choices[0].message.model_extra.get(k):
                    raw_text = str(res.choices[0].message.model_extra[k]).strip()
                    break
        candidate = _extract_code_block(raw_text)

        # 1. Full-file replacement validation
        if _is_valid_complete_replacement(candidate, broken_code):
            target_file.write_text(candidate, encoding="utf-8")
            print(f"  🌐 [LIVE NEBIUS ULTRA SELF-HEAL] Fixed {rel_file_path} via {NEMOTRON_ULTRA_MODEL}")
            return True, NEMOTRON_ULTRA_MODEL

        # 2. Surgical AST grafting fallback for partial/single-class patches
        spliced = _splice_ast_patch(broken_code, candidate)
        if spliced and _is_valid_complete_replacement(spliced, broken_code):
            target_file.write_text(spliced, encoding="utf-8")
            print(f"  🌐 [LIVE NEBIUS ULTRA SELF-HEAL] Surgically grafted fix into {rel_file_path} via {NEMOTRON_ULTRA_MODEL}")
            return True, NEMOTRON_ULTRA_MODEL

        print(f"  ❌ [Self-Heal Error] Ultra output for {rel_file_path} failed syntax/structural validation.")
        return False, NEMOTRON_ULTRA_MODEL
    except Exception as exc:
        print(f"  ❌ [Self-Heal Error] Live inference failed for {rel_file_path}: {exc}")
        return False, NEMOTRON_ULTRA_MODEL
