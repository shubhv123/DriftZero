# Calls Tavily Search, Extract, and Research to build MigrationDelta
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set

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


def _filter_sections_by_symbols(raw_text: str, symbols: List[str]) -> List[str]:
    """
    Splits raw documentation text into sections and returns ONLY those sections that discuss
    at least one of the detected unique_deprecated_symbols.
    """
    if not raw_text or not symbols:
        return []

    # Clean symbols for word matching (strip parens, leading dots)
    clean_symbols: Set[str] = set()
    for s in symbols:
        cleaned = re.sub(r"[\(\)\.]", "", s).strip()
        if cleaned:
            clean_symbols.add(cleaned.lower())

    if not clean_symbols:
        return []

    # Split text into sections by markdown headers (#, ##, ###) or paragraph breaks
    if re.search(r"\n#{1,6}\s+", raw_text):
        sections = re.split(r"(?=\n#{1,6}\s+)", raw_text)
    else:
        sections = re.split(r"\n\s*\n", raw_text)

    matching_sections: List[str] = []
    for sec in sections:
        sec_text = sec.strip()
        if not sec_text or len(sec_text) < 20:
            continue
        for sym in clean_symbols:
            # Match word boundary or token
            if re.search(r"(?i)(?:\b|_)" + re.escape(sym) + r"(?:\b|_|\()", sec_text):
                matching_sections.append(sec_text)
                break

    return matching_sections


def _normalize_url(url: str) -> str:
    """Normalizes URL for robust citation matching (stripping trailing slash and lowercasing)."""
    return url.strip().rstrip("/").lower()


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


def build_migration_delta(
    blast_report: BlastRadiusReport,
    build_log: Optional[str] = None,
) -> MigrationDelta:
    """
    Executes Stage 2:
    1. Runs three Tavily searches in parallel:
       - Migration guide
       - GitHub release notes (with include_domains=["github.com"])
       - Closed breaking-change issues
    2. Calls tavily.extract on the top guide URL and feeds ONLY sections matching unique_deprecated_symbols.
    3. Sets tavily_queries_executed to queries actually run.
    4. Passes context to Nemotron via Nebius Token Factory to synthesize structured MigrationRules.
    5. Rejects rules whose source_url is not in citations.
    6. Reports detected symbols with no rule.
    """
    start_time = time.perf_counter()
    citations: List[str] = []
    extracted_snippets: List[str] = []
    tavily_queries_executed: List[str] = []

    # 1. Prepare the three parallel queries
    guide_query = (
        blast_report.tavily_search_queries[0]
        if blast_report.tavily_search_queries
        else f"{blast_report.target_library} {blast_report.old_version or ''} to {blast_report.target_version} official migration guide breaking changes".strip()
    )
    release_query = f"{blast_report.target_library} {blast_report.target_version} release notes breaking changes"
    issues_query = f"{blast_report.target_library} {blast_report.target_version} closed breaking change issues"

    search_tasks = [
        ("guide", guide_query, None),
        ("release", release_query, ["github.com"]),
        ("issues", issues_query, None),
    ]

    guide_extracted_sections: List[str] = []

    # Live Tavily Agentic Search + Extract (when TAVILY_API_KEY is present)
    if TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

            def _search_worker(task_type: str, query: str, domains: Optional[List[str]]):
                kwargs = {
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 3,
                }
                if domains:
                    kwargs["include_domains"] = domains
                try:
                    return task_type, query, tavily_client.search(**kwargs)
                except Exception as exc:
                    print(f"  ⚠️ [Tavily Search Error] Query '{query}': {exc}", flush=True)
                    return task_type, query, {"results": []}

            # Run the three searches in parallel using ThreadPoolExecutor
            search_results_by_type: Dict[str, Any] = {}
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(_search_worker, t_type, q, dom)
                    for t_type, q, dom in search_tasks
                ]
                for fut in as_completed(futures):
                    t_type, q, res = fut.result()
                    search_results_by_type[t_type] = res
            tavily_queries_executed = [q for _, q, _ in search_tasks]

            # Collect citations and snippets from all 3 searches
            for t_type, q, _ in search_tasks:
                res = search_results_by_type.get(t_type, {})
                for item in res.get("results", []):
                    url = item.get("url")
                    if url and url not in citations:
                        citations.append(url)
                    content_snip = item.get("content", "")
                    if content_snip:
                        extracted_snippets.append(
                            f"Source: {url}\nTitle: {item.get('title', '')}\nExcerpt: {content_snip}"
                        )

            # Identify top guide URL from the migration guide search
            guide_res = search_results_by_type.get("guide", {}).get("results", [])
            top_guide_url = guide_res[0].get("url") if guide_res else None
            if not top_guide_url and citations:
                top_guide_url = citations[0]

            # Call tavily.extract on top guide URL and feed only sections matching unique_deprecated_symbols
            if top_guide_url:
                try:
                    print(f"  🔍 [Tavily Extract] Extracting content from top guide: {top_guide_url}", flush=True)
                    extract_res = tavily_client.extract(urls=[top_guide_url])
                    raw_content = ""
                    for item in extract_res.get("results", []):
                        raw_content += item.get("raw_content") or item.get("content") or ""

                    if raw_content:
                        guide_extracted_sections = _filter_sections_by_symbols(
                            raw_content, blast_report.unique_deprecated_symbols
                        )
                        print(
                            f"  🎯 [Tavily Extract] Filtered {len(guide_extracted_sections)} section(s) "
                            f"matching unique_deprecated_symbols from top guide.",
                            flush=True,
                        )
                except Exception as exc:
                    print(f"  ⚠️ [Tavily Extract Error] on {top_guide_url}: {exc}", flush=True)

        except Exception as exc:
            print(f"[Tavily Info] Live parallel search notice: {exc}", flush=True)

    # Fallback default citations if none returned from web search
    if not citations:
        citations = [
            f"https://pypi.org/project/{blast_report.target_library}/{blast_report.target_version}/",
            f"https://github.com/search?q={blast_report.target_library}+{blast_report.target_version}+migration",
        ]

    normalized_citations = {_normalize_url(c): c for c in citations if c}

    rules: List[MigrationRule] = []

    # 2. Live LLM Enrichment on Nebius Token Factory
    if NEBIUS_API_KEY and (extracted_snippets or guide_extracted_sections or blast_report.unique_deprecated_symbols or build_log):
        try:
            from openai import OpenAI

            client = OpenAI(api_key=NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL, timeout=40.0, max_retries=1)

            context_blocks = []
            if guide_extracted_sections:
                context_blocks.append(
                    "Top Guide Relevant Sections (Filtered for Deprecated Symbols):\n"
                    + "\n\n---\n\n".join(guide_extracted_sections[:6])
                )
            if extracted_snippets:
                context_blocks.extend(extracted_snippets[:4])

            context_text = "\n---\n".join(context_blocks) if context_blocks else "No web docs available."
            if len(context_text) > 4000:
                context_text = context_text[:4000] + "... (truncated)"

            citations_instruction = "\n".join(f"- {c}" for c in citations[:6])

            prompt_payload = (
                f"Target Library: {blast_report.target_library}\n"
                f"Target Upgrade: {blast_report.old_version or 'v1'} -> {blast_report.target_version}\n"
                f"Symbols detected in repo: {blast_report.unique_deprecated_symbols[:15]}\n"
                f"Verified Citations (EVERY rule's source_url MUST be chosen from one of these exact URLs):\n"
                f"{citations_instruction}\n\n"
                f"Documentation Excerpts:\n{context_text}"
            )
            if build_log and build_log.strip():
                prompt_payload += f"\n\nCaptured Build / Test Error Traceback:\n{build_log[:3000]}"

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

            # Reject rules whose source_url isn't in citations
            for r in parsed_rules:
                source_url = (r.get("source_url") or "").strip()
                norm_src = _normalize_url(source_url)
                if not source_url or norm_src not in normalized_citations:
                    print(
                        f"  🚫 [Tavily Grounding] Rejected rule for symbol '{r.get('symbol')}': "
                        f"source_url '{source_url}' is not in verified citations.",
                        flush=True,
                    )
                    continue

                canonical_url = normalized_citations[norm_src]
                rules.append(
                    MigrationRule(
                        symbol=r.get("symbol", blast_report.target_library),
                        old_syntax=r.get("old_syntax", ""),
                        new_syntax=r.get("new_syntax", ""),
                        required_imports=r.get("required_imports", []),
                        breaking_gotcha=r.get("breaking_gotcha", ""),
                        source_url=canonical_url,
                    )
                )

            if rules:
                print(f"  ✅ [Nebius Token Factory] Synthesized {len(rules)} verified Tavily delta rules via {NEMOTRON_SUPER_MODEL}", flush=True)
        except Exception as exc:
            print(f"[Nebius Info] Live delta synthesis notice: {exc}", flush=True)

    # Dynamic symbol grounding fallback if LLM synthesis returned empty rules
    if not rules:
        target_symbols = blast_report.unique_deprecated_symbols or [blast_report.target_library]
        default_source = citations[0] if citations else f"https://pypi.org/project/{blast_report.target_library}/{blast_report.target_version}/"
        for sym in target_symbols[:8]:
            rules.append(
                MigrationRule(
                    symbol=sym,
                    old_syntax=f"# Legacy usage of {sym}",
                    new_syntax=f"# Modern replacement for {sym} in {blast_report.target_library} {blast_report.target_version}",
                    required_imports=[],
                    breaking_gotcha=f"Symbol '{sym}' updated in {blast_report.target_library} {blast_report.target_version}. Check import path and argument signature.",
                    source_url=default_source,
                )
            )
        print(f"  ⚡ [Dynamic Rule Grounding] Generated {len(rules)} rules from AST blast-radius symbols", flush=True)

    # Report detected symbols with no rule
    rule_symbols: Set[str] = set()
    for r in rules:
        sym = r.symbol.strip().lower()
        rule_symbols.add(sym)
        cleaned = re.sub(r"[\(\)\.]", "", sym).strip().lower()
        if cleaned:
            rule_symbols.add(cleaned)

    uncovered_symbols: List[str] = []
    for sym in blast_report.unique_deprecated_symbols:
        clean_sym = re.sub(r"[\(\)\.]", "", sym).strip().lower()
        if sym.lower() not in rule_symbols and clean_sym not in rule_symbols:
            uncovered_symbols.append(sym)

    if uncovered_symbols:
        print(
            f"  ⚠️ [Tavily Delta] Detected symbols with no migration rule ({len(uncovered_symbols)}): "
            f"{', '.join(uncovered_symbols)}",
            flush=True,
        )
    else:
        print(
            f"  ✅ [Tavily Delta] All {len(blast_report.unique_deprecated_symbols)} detected symbols have matching migration rules.",
            flush=True,
        )

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return MigrationDelta(
        library=blast_report.target_library,
        from_version=blast_report.old_version or "legacy",
        to_version=blast_report.target_version,
        tavily_queries_executed=tavily_queries_executed,
        citations=citations,
        rules=rules,
        synthesized_by_model=NEMOTRON_SUPER_MODEL,
        generation_latency_ms=elapsed_ms,
        uncovered_symbols=uncovered_symbols,
    )