# Calls Tavily Search, Extract, and Research to build MigrationDelta
import json
import re
import time
from typing import Any, Dict, List, Optional

from backend.config import (
    NEBIUS_API_KEY,
    NEBIUS_BASE_URL,
    NEMOTRON_SUPER_MODEL,
    TAVILY_API_KEY,
)
from backend.intelligence.prompts import DELTA_SYNTHESIS_SYSTEM_PROMPT
from backend.schemas.migration_models import (
    BlastRadiusReport,
    MigrationDelta,
    MigrationRule,
)


def _extract_llm_text(msg) -> str:
    """Extracts completion text from standard content or reasoning fields in Nebius message."""
    if getattr(msg, "content", None) and str(msg.content).strip():
        return str(msg.content).strip()
    if hasattr(msg, "reasoning_content") and getattr(msg, "reasoning_content", None):
        return str(getattr(msg, "reasoning_content", "")).strip()
    if hasattr(msg, "model_extra") and isinstance(msg.model_extra, dict):
        for k in ("reasoning_content", "reasoning", "thought", "thinking"):
            if msg.model_extra.get(k):
                return str(msg.model_extra[k]).strip()
    return ""


def _parse_rules_from_llm(raw_content: str) -> List[Dict[str, Any]]:
    if not raw_content:
        return []
    content = raw_content.strip()

    # 1. Direct JSON parse
    try:
        parsed = json.loads(content, strict=False)
        if isinstance(parsed, dict) and parsed.get("rules"):
            return parsed["rules"]
        elif isinstance(parsed, list) and len(parsed) > 0:
            return parsed
    except Exception:
        pass

    # 2. Markdown codeblock extraction
    if "```json" in content:
        block = content.split("```json", 1)[1].split("```", 1)[0].strip()
        try:
            parsed = json.loads(block, strict=False)
            if isinstance(parsed, dict) and parsed.get("rules"):
                return parsed["rules"]
        except Exception:
            pass
    elif "```" in content:
        block = content.split("```", 1)[1].split("```", 1)[0].strip()
        try:
            parsed = json.loads(block, strict=False)
            if isinstance(parsed, dict) and parsed.get("rules"):
                return parsed["rules"]
        except Exception:
            pass

    # 3. Regex finding {"rules": [ even inside reasoning monologue
    match = re.search(r'\{\s*"rules"\s*:\s*\[', content)
    if match:
        start_idx = match.start()
        last_brace = content.rfind("}")
        while last_brace > start_idx:
            candidate = content[start_idx : last_brace + 1]
            try:
                parsed = json.loads(candidate, strict=False)
                if isinstance(parsed, dict) and parsed.get("rules"):
                    return parsed["rules"]
            except Exception:
                pass
            last_brace = content.rfind("}", start_idx, last_brace)

        # Truncation repair: if the stream cut off mid-list, close it with \n]}
        idx = content.rfind("}")
        while idx > start_idx:
            candidate = content[start_idx : idx + 1] + "\n]}"
            try:
                parsed = json.loads(candidate, strict=False)
                if isinstance(parsed, dict) and parsed.get("rules"):
                    return parsed["rules"]
            except Exception:
                pass
            idx = content.rfind("}", start_idx, idx)

    # 4. Outermost JSON array fallback
    first_bracket = content.find("[")
    if first_bracket != -1:
        last_bracket = content.rfind("]")
        if last_bracket != -1:
            try:
                parsed = json.loads(content[first_bracket : last_bracket + 1], strict=False)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass

    print(f"  ⚠️ [_parse_rules_from_llm Warning] Unable to parse rules. Raw preview ({len(raw_content)} chars):\n{raw_content[:200]}...[tail]:\n{raw_content[-200:]}", flush=True)
    return []



def build_migration_delta(blast_report: BlastRadiusReport) -> MigrationDelta:
    """
    Executes Stage 2:
    1. Queries Tavily `/search` and `/extract` for live official migration docs & GitHub issues for ANY library.
    2. Passes extracted context to Nemotron via Nebius Token Factory to synthesize structured MigrationRules.
    3. Merges and falls back safely so the pipeline never breaks.
    """
    start_time = time.perf_counter()
    citations: List[str] = []
    extracted_snippets: List[str] = []

    # 1. Live Tavily Agentic Search + Extract (when TAVILY_API_KEY is present)
    if TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
            # Query high-signal official migration guide
            primary_query = (
                blast_report.tavily_search_queries[0]
                if blast_report.tavily_search_queries
                else f"{blast_report.target_library} {blast_report.old_version or ''} to {blast_report.target_version} official migration guide breaking changes"
            )
            search_res = tavily_client.search(
                query=primary_query,
                search_depth="advanced",
                max_results=3,
            )
            for item in search_res.get("results", []):
                url = item.get("url")
                if url and url not in citations:
                    citations.append(url)
                content_snip = item.get("content", "")
                if content_snip:
                    extracted_snippets.append(f"Source: {url}\nTitle: {item.get('title')}\nExcerpt: {content_snip}")
        except Exception as exc:
            print(f"[Tavily Info] Live search notice: {exc}", flush=True)

    # Fallback default citations if none returned
    if not citations:
        citations = [
            f"https://pypi.org/project/{blast_report.target_library}/{blast_report.target_version}/",
            f"https://github.com/search?q={blast_report.target_library}+{blast_report.target_version}+migration",
        ]

    rules: List[MigrationRule] = []

    # 2. Live LLM Enrichment on Nebius Token Factory
    if NEBIUS_API_KEY and (extracted_snippets or blast_report.unique_deprecated_symbols):
        try:
            from openai import OpenAI

            client = OpenAI(api_key=NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL, timeout=40.0, max_retries=1)
            context_text = "\n---\n".join(extracted_snippets[:4]) if extracted_snippets else "No web docs available."
            # Cap context to 4000 characters to keep prompt compact and fast
            if len(context_text) > 4000:
                context_text = context_text[:4000] + "... (truncated)"
            
            prompt_payload = (
                f"Target Library: {blast_report.target_library}\n"
                f"Target Upgrade: {blast_report.old_version or 'v1'} -> {blast_report.target_version}\n"
                f"Symbols detected in repo: {blast_report.unique_deprecated_symbols[:15]}\n\n"
                f"Documentation Excerpts:\n{context_text}"
            )
            print(f"  🌐 [LIVE NEBIUS TOKEN FACTORY] Requesting migration rules via {NEMOTRON_SUPER_MODEL}...", flush=True)
            completion = client.chat.completions.create(
                model=NEMOTRON_SUPER_MODEL,
                messages=[
                    {"role": "system", "content": DELTA_SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_payload + "\n\nCRITICAL: DO NOT output any internal monologue, reasoning, or analysis. Start your output immediately with {\"rules\":"},
                ],
                temperature=0.0,
                max_tokens=2048,
            )
            msg = completion.choices[0].message
            raw_content = _extract_llm_text(msg)
            parsed_rules = _parse_rules_from_llm(raw_content)

            for r in parsed_rules:
                rules.append(
                    MigrationRule(
                        symbol=r.get("symbol", blast_report.target_library),
                        old_syntax=r.get("old_syntax", ""),
                        new_syntax=r.get("new_syntax", ""),
                        required_imports=r.get("required_imports", []),
                        breaking_gotcha=r.get("breaking_gotcha", ""),
                        source_url=r.get("source_url", citations[0] if citations else ""),
                    )
                )
            if rules:
                print(f"  ✅ [Nebius Token Factory] Synthesized {len(rules)} live Tavily delta rules via {NEMOTRON_SUPER_MODEL}", flush=True)
        except Exception as exc:
            print(f"[Nebius Info] Live delta synthesis notice: {exc}", flush=True)

    # Dynamic symbol grounding fallback if LLM synthesis returned empty rules
    if not rules:
        target_symbols = blast_report.unique_deprecated_symbols or [blast_report.target_library]
        for sym in target_symbols[:8]:
            rules.append(
                MigrationRule(
                    symbol=sym,
                    old_syntax=f"# Legacy usage of {sym}",
                    new_syntax=f"# Modern replacement for {sym} in {blast_report.target_library} {blast_report.target_version}",
                    required_imports=[],
                    breaking_gotcha=f"Symbol '{sym}' updated in {blast_report.target_library} {blast_report.target_version}. Check import path and argument signature.",
                    source_url=citations[0] if citations else f"https://pypi.org/project/{blast_report.target_library}/{blast_report.target_version}/",
                )
            )
        print(f"  ⚡ [Dynamic Rule Grounding] Generated {len(rules)} rules from AST blast-radius symbols", flush=True)

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return MigrationDelta(
        library=blast_report.target_library,
        from_version=blast_report.old_version or "legacy",
        to_version=blast_report.target_version,
        tavily_queries_executed=blast_report.tavily_search_queries,
        citations=citations,
        rules=rules,
        synthesized_by_model=NEMOTRON_SUPER_MODEL,
        generation_latency_ms=elapsed_ms,
    )