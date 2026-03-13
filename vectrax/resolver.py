"""
Vectrax Resolver — Cognitive Chat Resolution Engine
=====================================================
Classifies user messages and resolves them through 3 modes:

  MEMORY  — plain note/statement, ingested as a star
  LOCAL   — question answerable from existing memory stars
  ONLINE  — question requiring external/current information

Components:
  classify()         — determine message intent
  resolve_local()    — search memory for relevant stars
  resolve_online()   — search web via DuckDuckGo, extract snippets
  resolve()          — orchestrator: classify → route → answer
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger("vectrax.resolver")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A reference returned from online research."""
    title: str
    url: str
    snippet: str


@dataclass
class Resolution:
    """Result of resolving a user message."""
    mode: str                   # "memory" | "local" | "online"
    answer: str = ""            # internal/debug answer (may list sources)
    sovereign_answer: str = ""  # clean user-facing answer (no source info)
    sources: List[Source] = field(default_factory=list)
    context_stars: int = 0      # how many stars were consulted
    search_query: str = ""      # query used for online search (if any)
    fallback_from: str = ""     # non-empty if mode was escalated (e.g. "local")
    top_score: float = 0.0      # best relevance score from local search
    engines_used: List[str] = field(default_factory=list)

# ---------------------------------------------------------------------------
# 1. CLASSIFIER
# ---------------------------------------------------------------------------

# Patterns indicating a question at the START of the text
_QUESTION_STARTS = re.compile(
    r"^(what|who|where|when|why|how|is|are|was|were|do|does|did|can|could|"
    r"should|would|will|which|tell me|explain|describe|define|compare|analyze|summarize|"
    r"qué|quién|quien|cómo|como|cuándo|cuando|dónde|donde|por qué|por que|cuál|cual|cuánto|cuanto|"
    r"explica|explícame|explicame|dime|analiza|compara|resume|resúmeme|resumeme|describe)\b",
    re.IGNORECASE,
)

# Semantic intent markers that indicate a query ANYWHERE in the text.
# These catch imperative verbs and accented interrogatives even mid-sentence
# (e.g. "por favor explica cómo funciona la gravedad").
_QUERY_INTENT = re.compile(
    r"(?:"
    r"\b(?:explica|explícame|explicame|dime|analiza|compara|resume|resúmeme|resumeme|describe)\b"
    r"|\b(?:quién|quien|qué|cómo|cuándo|dónde|por qué|cuál|cuánto)\b"
    r"|\b(?:what is|who is|how does|how do|how is|what are|tell me|explain)\b"
    r")",
    re.IGNORECASE,
)

# Explicit self-memory references → LOCAL
# These phrases clearly indicate the user is asking about their OWN history/data.
_LOCAL_KEYWORDS = re.compile(
    r"(\bwhat did i\b|\bwhat i said\b|\bwhat have i\b"
    r"|\bmy (messages|history|notes|data|stars|memory|conversations?)\b"
    r"|\bqué dije\b|\bqué te dije\b|\bqué hablamos\b|\bqué recuerdas\b"
    r"|\bmi (historial|memoria|mensajes|notas|conversación|datos)\b"
    r"|\besta conversación\b|\bthis conversation\b"
    r"|\bdo you remember\b|\brecuerdas\b"
    r"|\blo que te conté\b|\blo que escribí\b"
    r"|\bmy previous\b|\bmy last\b)",
    re.IGNORECASE,
)


def classify(text: str) -> str:
    """
    Classify a user message into one of: 'memory', 'local', 'online'.

    Logic (priority order):
      1. If it references the user's own memory explicitly → local
      2. If it is a question / query (punctuation OR start pattern OR
         semantic intent marker anywhere in text) → online
      3. Otherwise → memory (plain note/statement for ingestion)

    Intent detection prioritises semantics over punctuation:
    "explica cómo funciona la gravedad" is recognised as a query even
    without a question mark.
    """
    text_stripped = text.strip()
    has_question_mark = "?" in text_stripped or "¿" in text_stripped
    has_question_start = bool(_QUESTION_STARTS.match(text_stripped))
    has_query_intent = bool(_QUERY_INTENT.search(text_stripped))
    has_local_keywords = bool(_LOCAL_KEYWORDS.search(text_stripped))

    is_question = has_question_mark or has_question_start or has_query_intent

    logger.debug(
        "classify(%r): ?=%s start=%s intent=%s local=%s → question=%s",
        text_stripped[:60], has_question_mark, has_question_start,
        has_query_intent, has_local_keywords, is_question,
    )

    # Self-memory reference takes priority → LOCAL
    # Local keywords alone are sufficient (user asking about their own data).
    if has_local_keywords:
        return "local"
    # Any other question / query → ONLINE (factual default)
    if is_question:
        return "online"
    return "memory"


# ---------------------------------------------------------------------------
# 2. LOCAL RESOLVER — search existing memory stars
# ---------------------------------------------------------------------------

def resolve_local(
    text: str,
    channel: str,
    owner: str,
    top_k: int = 5,
    threshold: float = 0.40,
) -> Resolution:
    """
    Search the user's existing stars for content relevant to the question.
    Uses the same embedding + cosine similarity as the ingest pipeline.
    """
    from vectrax import db
    from vectrax.embeddings import decode_embedding, embed, find_similar

    db.init_db()
    query_vec = embed(text)
    stars = db.get_all_stars(channel=channel, owner=owner)

    star_embeddings: List[Tuple[str, object]] = [
        (s.id, decode_embedding(s.embedding))
        for s in stars
        if s.embedding is not None
    ]

    matches = find_similar(query_vec, star_embeddings, threshold=threshold)
    matches = matches[:top_k]

    if not matches:
        lang = _detect_lang(text)
        labels = _get_labels(lang)
        return Resolution(
            mode="local",
            answer="No relevant memory found.",
            sovereign_answer=labels["no_memory"],
            context_stars=0,
            top_score=0.0,
        )

    top_score = matches[0][1] if matches else 0.0

    # Build debug answer (with scores) and sovereign answer (clean)
    star_map = {s.id: s for s in stars}
    debug_parts = []
    clean_parts = []
    for star_id, score in matches:
        s = star_map.get(star_id)
        if s:
            debug_parts.append(f"• {s.content} (relevance: {score:.0%}, layer: {s.layer})")
            clean_parts.append(s.content)

    answer = "Based on your memory:\n\n" + "\n".join(debug_parts)
    lang = _detect_lang(text)
    sovereign = _synthesize_local(clean_parts, lang=lang)

    return Resolution(
        mode="local",
        answer=answer,
        sovereign_answer=sovereign,
        context_stars=len(matches),
        top_score=top_score,
    )


# ---------------------------------------------------------------------------
# LANGUAGE DETECTION
# ---------------------------------------------------------------------------

_ES_MARKERS = re.compile(
    r"[áéíóúñü¿¡]"
    r"|\b(el|la|los|las|del|una|unos|un|es|fue|son|está|por|para|con|como|que|"
    r"pero|más|sobre|entre|desde|hasta|tiene|puede|había|ser|también|y|o|se)\b",
    re.IGNORECASE,
)

# Fast-path: unambiguously Spanish verb/interrogative at start of text
_ES_VERB_START = re.compile(
    r"^(?:explica|explícame|explicame|dime|analiza|compara|resume|resúmeme|"
    r"quién|qué|cómo|cuándo|dónde|cuál|cuánto)\b",
    re.IGNORECASE,
)


def _detect_lang(text: str) -> str:
    """Detect language of text. Returns 'es' or 'en'."""
    stripped = text.strip()
    # Fast-path: starts with unambiguously Spanish verb/interrogative
    if _ES_VERB_START.match(stripped):
        return "es"
    es_hits = len(_ES_MARKERS.findall(stripped))
    word_count = max(len(stripped.split()), 1)
    # If >15% of words trigger ES markers → Spanish
    if es_hits / word_count > 0.15:
        return "es"
    return "en"


def _sentence_lang(sentence: str) -> str:
    """Detect language of a single sentence."""
    return _detect_lang(sentence)


# Labels by language
_LABELS = {
    "es": {
        "key_points": "Puntos clave:",
        "comparison": "Comparación:",
        "no_info": "No encontré información suficiente para responder eso con certeza.",
        "no_memory": "No tengo información sobre eso en mi memoria.",
        "memory_single": "En tu memoria encontré esto:",
        "memory_multi": "Esto es lo que tengo en tu memoria:",
        "memory_extra": "registros adicionales",
        "registered": "Registrado.",
        "updated": "Actualizado.",
        "disambig": "puede referirse a:",
    },
    "en": {
        "key_points": "Key points:",
        "comparison": "Comparison:",
        "no_info": "I couldn't find enough information to answer that with certainty.",
        "no_memory": "I don't have information about that in my memory.",
        "memory_single": "Found this in your memory:",
        "memory_multi": "Here's what I have in your memory:",
        "memory_extra": "additional records",
        "registered": "Registered.",
        "updated": "Updated.",
        "disambig": "can refer to:",
    },
}


def _get_labels(lang: str) -> dict:
    return _LABELS.get(lang, _LABELS["en"])


# ---------------------------------------------------------------------------
# SOVEREIGN SYNTHESIS — clean answers without source attribution
# ---------------------------------------------------------------------------

# Boilerplate phrases to strip from snippets
_BOILERPLATE = re.compile(
    r"(click here|read more|learn more|subscribe|sign up|log in|cookie|"
    r"privacy policy|terms of service|advertisement|sponsored|\.\.\.$)",
    re.IGNORECASE,
)

# CTA / promotional sentence starts to discard
_CTA_STARTS = re.compile(
    r"^(descubre|discover|encuentra|find out|explore|visit|shop|buy|get|"
    r"aprende|check out|see |view |watch |haz |mira |conoce |entra )",
    re.IGNORECASE,
)

# Article / blog meta phrases — stripped from snippets before synthesis
_ARTICLE_META = re.compile(
    r"(?:en este artículo|in this article|in this post|in this guide|"
    r"exploraremos|we will explore|we'll explore|we explore|"
    r"vamos a ver|vamos a explorar|a continuación veremos|"
    r"in this tutorial|in this section|as we'll see|"
    r"sigue leyendo|keep reading|read on|"
    r"te explicamos|te contamos|te mostramos|"
    r"hoy hablaremos|today we'll|let's take a look|"
    r"let's explore|let's dive|let us explore|"
    r"this article explains|este artículo explica|"
    r"here we discuss|aquí discutimos|aquí explicamos|"
    r"you'll learn|aprenderás|en esta guía|in this review)",
    re.IGNORECASE,
)

# First-person article voice — discard entire sentence if it starts this way
_ARTICLE_VOICE = re.compile(
    r"^(?:en este artículo|in this article|this article|este artículo|"
    r"here we |aquí |in this post|in this guide|en esta guía|"
    r"let's |vamos a explorar)",
    re.IGNORECASE,
)


def _strip_article_meta(text: str) -> str:
    """Remove article/blog meta phrases from text."""
    cleaned = _ARTICLE_META.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = cleaned.lstrip(",;:. ")
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


# ---------------------------------------------------------------------------
# COMPLEXITY DETECTION
# ---------------------------------------------------------------------------

_SIMPLE_Q = re.compile(
    r"^(?:qué es|que es|what is|who is|quién es|quien es|"
    r"define|what does\b.+\bmean|qué significa|qué son|what are)\b",
    re.IGNORECASE,
)

_COMPARISON_Q = re.compile(
    r"(?:\bcompara\b|\bcompare\b|\bvs\b|\bversus\b|\bdiferencia entre\b|"
    r"\bdifference between\b|\bfrente a\b)",
    re.IGNORECASE,
)


def _query_complexity(text: str) -> str:
    """
    Classify question complexity.
    Returns: 'simple', 'comparison', or 'complex'.
    """
    t = text.strip()
    if _COMPARISON_Q.search(t):
        return "comparison"
    if _SIMPLE_Q.match(t):
        return "simple"
    # Short queries without complex verbs → simple
    if len(t.split()) <= 4 and not re.search(
        r"\b(explica|explain|analiza|analyze|cómo funciona|como funciona|"
        r"how does|por qué|por que|why)\b", t, re.I,
    ):
        return "simple"
    return "complex"


# ---------------------------------------------------------------------------
# DISAMBIGUATION
# ---------------------------------------------------------------------------

_PERSON_IND = re.compile(
    r"\b(born|died|inventor|scientist|physicist|engineer|biography|"
    r"nació|murió|inventó|científico|físico|ingeniero|biografía|"
    r"was a |fue un |fue una )",
    re.IGNORECASE,
)

_COMPANY_IND = re.compile(
    r"\b(Inc|Corp|LLC|company|empresa|stock|revenue|"
    r"products|productos|founded|fundada|headquarters|"
    r"sede|market|shares|CEO|startup|brand|marca)",
    re.IGNORECASE,
)

_SCIENCE_IND = re.compile(
    r"\b(unit|unidad|formula|fórmula|physics|física|chemistry|química|"
    r"SI unit|measurement|medida|equation|ecuación|magnetic|magnético)",
    re.IGNORECASE,
)


def _detect_ambiguity(
    query: str, sources: List[Source], lang: str,
) -> Optional[str]:
    """
    Detect if search results suggest multiple interpretations for a short query.
    Returns a disambiguation preamble or None.
    """
    # Strip common question prefixes to extract the core subject
    subject = re.sub(
        r"^(?:qué es|que es|what is|who is|quién es|quien es|"
        r"define|dime qué es|dime que es|tell me about|tell me what is|"
        r"quien inventó|quién inventó|who invented|who created)\s+",
        "", query, flags=re.I,
    ).strip().rstrip("?").strip()

    # Only check short subjects (1–3 words)
    if len(subject.split()) > 3:
        return None

    # Skip if the query already has disambiguating context
    if re.search(
        r"\b(empresa|company|inventor|persona|person|unit|unidad|"
        r"marca|brand|car|auto|coche|molécula|molecule|supplement|suplemento)\b",
        query, re.I,
    ):
        return None

    # Scan source text for category signals
    all_text = " ".join(s.title + " " + s.snippet for s in sources)
    cats: List[Tuple[str, str]] = []
    if _PERSON_IND.search(all_text):
        cats.append(("persona/inventor" if lang == "es" else "person/inventor",))
    if _COMPANY_IND.search(all_text):
        cats.append(("empresa/marca" if lang == "es" else "company/brand",))
    if _SCIENCE_IND.search(all_text):
        cats.append(("concepto científico" if lang == "es" else "scientific concept",))

    if len(cats) < 2:
        return None

    labels = _get_labels(lang)
    lines = [f"\"{subject}\" {labels['disambig']}"]
    for (cat_label,) in cats:
        lines.append(f"• {subject} ({cat_label})")
    return "\n".join(lines)


def _clean_snippet(text: str) -> str:
    """Strip boilerplate, article meta, and trailing junk from a snippet."""
    text = _BOILERPLATE.sub("", text).strip()
    text = _strip_article_meta(text)
    # Remove trailing fragments (incomplete sentences ending without punctuation)
    if text and text[-1] not in ".!?\"')'":
        last_period = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if last_period > len(text) // 3:
            text = text[: last_period + 1]
    return text.strip()


def _extract_sentences(
    snippets: List[str],
    max_sentences: int = 12,
    lang: str = "es",
) -> List[str]:
    """Extract, filter by language, and deduplicate informative sentences."""
    all_sentences = []
    for snippet in snippets:
        cleaned = _clean_snippet(snippet)
        if not cleaned:
            continue
        sents = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in sents:
            s = s.strip()
            if len(s) < 40:
                continue
            if s.endswith("?") or s.startswith("¿"):
                continue
            if _CTA_STARTS.match(s):
                continue
            if _ARTICLE_VOICE.match(s):
                continue
            if s.startswith("(") and len(s) < 60:
                continue
            all_sentences.append(s)

    # Filter by target language — keep only sentences matching the query language
    filtered = [s for s in all_sentences if _sentence_lang(s) == lang]
    # If filtering removed everything, fall back to unfiltered
    if not filtered:
        filtered = all_sentences

    # Deduplicate by prefix similarity
    seen = set()
    unique = []
    for s in filtered:
        key = s[:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:max_sentences]


def _synthesize_online(
    snippets: List[str],
    lang: str = "es",
    query: str = "",
    sources: Optional[List[Source]] = None,
) -> str:
    """
    Build a structured, depth-aware answer from web snippets.

    Depth is determined by query complexity:
      simple     → definition (1–2 sentences) + max 3 key facts
      comparison → lead paragraph + comparison bullets
      complex    → full lead (2–3 sentences) + key points + context

    If the query is ambiguous (short term with divergent results),
    a disambiguation preamble is prepended.

    All content in the same language as the user's question.
    No source names, no links, no engine references, no article meta.
    """
    labels = _get_labels(lang)
    sentences = _extract_sentences(snippets, max_sentences=12, lang=lang)
    if not sentences:
        return labels["no_info"]

    complexity = _query_complexity(query) if query else "complex"
    parts: List[str] = []

    # Disambiguation preamble (if applicable)
    if sources and query:
        disambig = _detect_ambiguity(query, sources, lang)
        if disambig:
            parts.append(disambig)
            parts.append("")

    # --- SIMPLE: definition + max 3 bullets ---
    if complexity == "simple":
        lead = sentences[:2]
        parts.append(" ".join(lead))
        rest = sentences[2:5]
        if rest:
            parts.append("")
            parts.append(labels["key_points"])
            for kp in rest:
                if len(kp) > 200:
                    dot = kp.find(".", 80)
                    if dot > 0:
                        kp = kp[: dot + 1]
                parts.append(f"• {kp}")

    # --- COMPARISON: lead + comparison bullets ---
    elif complexity == "comparison":
        lead = sentences[:2]
        parts.append(" ".join(lead))
        rest = sentences[2:]
        if rest:
            parts.append("")
            parts.append(labels["comparison"])
            for kp in rest[:6]:
                if len(kp) > 250:
                    dot = kp.find(".", 100)
                    if dot > 0:
                        kp = kp[: dot + 1]
                parts.append(f"• {kp}")

    # --- COMPLEX: full lead + key points + context ---
    else:
        lead_count = min(3, len(sentences))
        lead = sentences[:lead_count]
        rest = sentences[lead_count:]

        parts.append(" ".join(lead))

        if rest:
            key_points = rest[:5]
            parts.append("")
            parts.append(labels["key_points"])
            for kp in key_points:
                if len(kp) > 250:
                    dot = kp.find(".", 100)
                    if dot > 0:
                        kp = kp[: dot + 1]
                parts.append(f"• {kp}")

            extra = rest[5:8]
            if extra:
                context = " ".join(extra)
                if len(context) > 30:
                    parts.append("")
                    parts.append(context)

    return "\n".join(parts)


def _synthesize_local(star_contents: List[str], lang: str = "es") -> str:
    """
    Build a structured answer from the user's memory stars.
    Natural voice, no relevance scores, no layer names.
    """
    labels = _get_labels(lang)

    if not star_contents:
        return labels["no_memory"]

    # Single match → direct answer
    if len(star_contents) == 1:
        return f"{labels['memory_single']}\n\n{star_contents[0]}"

    # Multiple matches → structured list
    parts = [labels["memory_multi"]]
    parts.append("")
    for c in star_contents[:5]:
        parts.append(f"• {c}")

    if len(star_contents) > 5:
        parts.append(f"\n(+{len(star_contents) - 5} {labels['memory_extra']})")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 3. ONLINE RESOLVER — DuckDuckGo search
# ---------------------------------------------------------------------------

_DDG_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = "Vectrax/1.0 (Cognitive Memory Platform)"


def _search_duckduckgo(query: str, max_results: int = 5) -> List[Source]:
    """
    Search DuckDuckGo HTML endpoint (no API key required).
    Parse results from the HTML response using regex (no BeautifulSoup needed).
    """
    import requests

    try:
        resp = requests.post(
            _DDG_URL,
            data={"q": query},
            headers={"User-Agent": _USER_AGENT},
            timeout=8,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []

    body = resp.text
    sources = []

    # Extract result blocks: <a class="result__a" href="...">title</a>
    # and <a class="result__snippet">snippet</a>
    result_blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        body,
        re.DOTALL,
    )

    for url, raw_title, raw_snippet in result_blocks[:max_results]:
        # DuckDuckGo wraps URLs in a redirect — extract the actual URL
        actual_url = url
        uddg_match = re.search(r'uddg=([^&]+)', url)
        if uddg_match:
            from urllib.parse import unquote
            actual_url = unquote(uddg_match.group(1))

        title = _strip_html(raw_title).strip()
        snippet = _strip_html(raw_snippet).strip()

        if title and snippet:
            sources.append(Source(title=title, url=actual_url, snippet=snippet))

    return sources


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    cleaned = re.sub(r'<[^>]+>', '', text)
    return html.unescape(cleaned)


def resolve_online(
    text: str,
    channel: str,
    owner: str,
) -> Resolution:
    """
    Build a search query from the user's question, search the web,
    and compose an answer from the top results.
    """
    # Build query: use the question directly (DuckDuckGo handles natural language)
    query = text.strip().rstrip("?").strip()
    if len(query) > 200:
        query = query[:200]

    sources = _search_duckduckgo(query, max_results=5)

    lang = _detect_lang(text)
    labels = _get_labels(lang)

    if not sources:
        return Resolution(
            mode="online",
            answer="No online results found.",
            sovereign_answer=labels["no_info"],
            sources=[],
            search_query=query,
            engines_used=["duckduckgo"],
        )

    # Debug answer (numbered sources — internal only)
    debug_parts = []
    for i, src in enumerate(sources[:5], 1):
        debug_parts.append(f"{i}. **{src.title}**\n   {src.snippet}")
    answer = "DEBUG sources:\n\n" + "\n\n".join(debug_parts)

    # Sovereign answer — clean synthesis in the user's language
    lang = _detect_lang(text)
    snippets = [src.snippet for src in sources[:5]]
    sovereign = _synthesize_online(snippets, lang=lang, query=text, sources=sources)

    return Resolution(
        mode="online",
        answer=answer,
        sovereign_answer=sovereign,
        sources=sources,
        search_query=query,
        engines_used=["duckduckgo"],
    )


# ---------------------------------------------------------------------------
# 4. ORCHESTRATOR
# ---------------------------------------------------------------------------

# Minimum top-match relevance score for local results to be considered useful.
# Below this, the local answer is treated as low-confidence and triggers
# an automatic fallback to online research.
_LOCAL_RELEVANCE_THRESHOLD = 0.55


def resolve(
    text: str,
    channel: str,
    owner: str,
) -> Resolution:
    """
    Main entry point: classify → route → resolve.

    - memory → returns empty Resolution (caller handles ingest)
    - local  → searches memory stars; falls back to online if
               no matches OR top match relevance < threshold
    - online → searches the web
    """
    mode = classify(text)
    logger.info("Resolver: text=%r → mode=%s (channel=%s, owner=%s)",
                text[:60], mode, channel, owner)

    if mode == "memory":
        return Resolution(mode="memory")

    if mode == "local":
        result = resolve_local(text, channel, owner)
        # Fallback: no matches OR low-relevance top match
        if result.context_stars == 0 or result.top_score < _LOCAL_RELEVANCE_THRESHOLD:
            logger.info(
                "Resolver: local insufficient (stars=%d, top_score=%.2f), "
                "falling back to online",
                result.context_stars, result.top_score,
            )
            result = resolve_online(text, channel, owner)
            result.fallback_from = "local"
        return result

    # mode == "online"
    return resolve_online(text, channel, owner)
