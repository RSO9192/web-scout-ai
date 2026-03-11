# web-scout-ai

**The missing middle ground between web search APIs and deep research agents.**

Most AI tools give you one of two options: fast search APIs that return shallow snippets, or heavyweight research agents that take minutes and cost dollars per query. `web-scout-ai` fills the gap by giving you a streamlined pipeline that **automatically gets the right URLs** and **automatically handles complex file types**. It searches, scrapes, reads documents, evaluates coverage, and synthesizes findings into a sourced answer, all in a single async call that typically completes in 15-40 seconds.

```python
from web_scout import run_web_research

result = await run_web_research(
    query="What regulations protect mangrove ecosystems in Southeast Asia?",
    models={
        "web_researcher": "gemini/gemini-3.0-flash-preview",
        "content_extractor": "gemini/gemini-3.0-flash-preview",
    },
)
print(result.synthesis)   # Coherent narrative with citations
print(result.scraped)     # Full extracted content from each source
print(result.queries)     # Every search query that was executed
```

## Core Strengths

### 1. Automatically Gets the Right URLs
You don't need to manually feed it links. You pass a high-level question, and the tool:
- Uses an LLM to generate targeted search engine queries.
- Executes searches via Serper or DuckDuckGo.
- Interleaves and ranks the resulting URLs.
- Automatically evaluates if the scraped content fully answers the query. If there are gaps, it generates new search queries and fetches more URLs until the answer is complete.

### 2. Automatically Handles Complex File Types
Most web scrapers break on PDFs or single-page applications. `web-scout-ai` seamlessly handles:
- **Static HTML** (fast HTTP fetches)
- **JS-rendered SPAs** (headless Playwright browser)
- **Real Documents** (PDFs, DOCX, PPTX, XLSX via `docling`)
- **Scanned PDFs** (vision LLM fallback to extract text from screenshots)

### 3. Plug-and-Play Tool for Any Agent
Designed from the ground up to be called by other AI agents, not just as a standalone script.
- **One Function Call:** A single `run_web_research` async function.
- **Structured Output:** Returns a predictable Pydantic model (`WebResearchResult`).
- **Framework Agnostic:** Works flawlessly with OpenAI Agents SDK, LangChain, LlamaIndex, or your custom agent loop.
- **Model Agnostic:** Uses LiteLLM under the hood, so it works with OpenAI, Anthropic, Gemini, Groq, Mistral, or local models.

### 4. Multiple Content Extraction Methods
The tool doesn't just rely on search. It supports multiple ways to gather content:
- **Open Web Search:** Queries search engines and scrapes the best results.
- **Domain-Restricted Search:** Limits searches to specific websites (e.g., only `iucn.org` or `un.org`).
- **Direct URL Extraction:** Skip the search step entirely and just extract and synthesize content from a specific webpage or document link.

## Why web-scout-ai?

### The problem with existing tools

| Tool | What you get | What's missing |
| --- | --- | --- |
| **Tavily / Exa** | Search snippets via proprietary API | No actual page content. No synthesis. Vendor lock-in. Paid per query. |
| **Jina Reader** | Single URL to markdown | No search. No multi-source reasoning. No synthesis. |
| **Firecrawl** | Single URL to markdown (paid SaaS) | No search. No synthesis. Requires hosting or SaaS subscription. |
| **ScrapeGraphAI** | LLM-driven single-page extraction | No web search. No cross-source synthesis. Single page at a time. |
| **GPT-Researcher** | Deep multi-agent reports (2000+ words) | 1-3+ minutes per query. $0.05-0.10+ per report. Heavy LangChain dependency. Overkill for most questions. |
| **LangChain/LlamaIndex tools** | Building blocks you wire together | No integrated pipeline. You build and maintain the glue code. |

### What web-scout-ai does differently

**It actually reads the pages.** Search APIs return 200-character snippets. web-scout-ai scrapes each page, extracts the relevant content with a dedicated LLM sub-agent, and returns ~5,000 characters of focused, query-relevant content per source — not just a snippet.

**It handles real documents.** PDFs, DOCX, PPTX, XLSX — not just HTML. Government reports, academic papers, UN documents — the kind of sources that matter for serious research but that most tools silently skip.

**It closes the loop.** Search → Scrape → Evaluate → Iterate → Synthesize. If the first round of sources doesn't fully answer the query, it generates new search queries targeting the gaps and scrapes more pages. Most tools stop after search.

**It's deterministic.** No unbounded agentic loops. No unpredictable costs. The pipeline has a fixed structure with circuit breakers at every stage. You know what it will do and what it will cost.

**It works with any LLM.** OpenAI, Anthropic, Google Gemini, Mistral, Groq, DeepSeek, Together, local models via Ollama — anything [LiteLLM](https://docs.litellm.ai/docs/providers) supports. No vendor lock-in. Mix and match providers across pipeline steps.

**It's a single function call.** Designed as a plug-and-play tool for AI agents, not a framework you need to learn. One function, one result type, zero configuration beyond model names.

## How it works

```
Query
 │
 ├─ Generate search queries (LLM)
 ├─ Search the web (Serper or DuckDuckGo)
 ├─ Select best URLs (interleaved from multiple queries)
 ├─ Scrape & extract in parallel
 │   ├─ Static HTML → fast HTTP fetch (no browser)
 │   ├─ JS/SPA pages → Playwright browser
 │   ├─ PDFs → docling (text layer, no OCR)
 │   ├─ DOCX/PPTX/XLSX → docling
 │   └─ Scanned PDFs → vision LLM fallback (screenshot → extract)
 ├─ Evaluate coverage (LLM) — are there gaps?
 │   └─ If gaps → generate targeted queries → search & scrape again
 ├─ Synthesize findings (LLM)
 │
 └─ WebResearchResult
      ├─ synthesis: str (coherent answer)
      ├─ scraped: list[UrlEntry] (sources with full extracted content)
      ├─ scrape_failed: list[UrlEntry]
      ├─ snippet_only: list[UrlEntry]
      └─ queries: list[SearchQuery]
```

Every step has timeouts, circuit breakers, and deduplication. URL validation (HEAD + GET) skips dead links, paywalls, binary files, and blocked domains before any expensive processing starts.

## Installation

```bash
pip install web-scout-ai
web-scout-setup
```

The first command installs all dependencies including document extraction (PDF, DOCX, PPTX, XLSX via docling) and both search backends (Serper and DuckDuckGo). The second command installs the Chromium browser needed for scraping JS-rendered pages.

## Quick start

```python
import asyncio
from web_scout import run_web_research

async def main():
    result = await run_web_research(
        query="What are the main threats to coral reefs worldwide?",
        models={
            "web_researcher": "gemini/gemini-2.0-flash",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
    )

    print(result.synthesis)

    for source in result.scraped:
        print(f"  - {source.title}: {source.url}")

asyncio.run(main())
```

## Configuration

### Models

Pass a `models` dict to configure which LLM handles each pipeline step. All model strings follow the [LiteLLM naming convention](https://docs.litellm.ai/docs/providers):

```python
models = {
    # Required
    "web_researcher": "openai/gpt-4o",              # query generation, coverage evaluation, synthesis
    "content_extractor": "gemini/gemini-2.0-flash",  # page content extraction sub-agent

    # Optional overrides (default to web_researcher)
    "query_generator": "anthropic/claude-sonnet-4-20250514",
    "coverage_evaluator": "openai/gpt-4o-mini",
    "synthesiser": "anthropic/claude-sonnet-4-20250514",

    # Optional: vision fallback for scanned PDFs / empty JS pages
    "vision_fallback": "gemini/gemini-2.0-flash",
}
```

You can mix providers — e.g. use a cheap fast model for extraction and a stronger model for synthesis.

### Environment variables

Set the API key for your chosen provider(s):

```bash
# Search backend
export SERPER_API_KEY="..."          # for Serper (Google results) — or use DuckDuckGo (free, no key)

# LLM providers (set the ones you use)
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export MISTRAL_API_KEY="..."
export GROQ_API_KEY="..."
# ... any LiteLLM-supported provider
```

### Research modes

```python
# 1. Open web search (default)
result = await run_web_research(
    query="latest IPCC findings on sea level rise",
    models=models,
)

# 2. Domain-restricted search — only search within specific sites
result = await run_web_research(
    query="endemic species conservation programs",
    models=models,
    include_domains=["iucn.org", "wwf.org"],
)

# 3. Direct URL extraction — skip search, extract a specific page or document
result = await run_web_research(
    query="key findings from this report",
    models=models,
    direct_url="https://example.org/biodiversity-report.pdf",
)
```

### Search backends

```python
# Serper (default) — Google-quality results, requires SERPER_API_KEY
result = await run_web_research(query=..., models=..., search_backend="serper")

# DuckDuckGo — zero config, no API key needed
result = await run_web_research(query=..., models=..., search_backend="duckduckgo")
```

### Domain expertise

Provide domain context to improve query generation and synthesis quality:

```python
result = await run_web_research(
    query="red list status of Panthera tigris subspecies",
    models=models,
    domain_expertise="conservation biology and IUCN Red List assessments",
)
```

## Use as an agent tool

`web-scout-ai` is designed to be called by AI agents. One function, structured output, async-native:

```python
from agents import Agent, Runner, function_tool
from web_scout import run_web_research

@function_tool
async def research(query: str) -> str:
    """Search the web and return a synthesized answer with sources."""
    result = await run_web_research(
        query=query,
        models={
            "web_researcher": "gemini/gemini-2.0-flash",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
        search_backend="duckduckgo",
    )
    sources = "\n".join(f"- {s.url}" for s in result.scraped)
    return f"{result.synthesis}\n\nSources:\n{sources}"

agent = Agent(
    name="researcher",
    model="gpt-4o",
    tools=[research],
    instructions="Use the research tool to answer questions with up-to-date web information.",
)
```

Works with any agent framework — OpenAI Agents SDK, LangChain, LlamaIndex, or your own. It's just an async function that returns a Pydantic model.

## Output structure

`run_web_research` returns a `WebResearchResult`:

```python
class WebResearchResult(BaseModel):
    synthesis: str                     # Coherent narrative answering the query
    scraped: list[UrlEntry]            # Sources with full extracted content (~5000 chars each)
    scrape_failed: list[UrlEntry]      # URLs where scraping failed
    snippet_only: list[UrlEntry]       # Search results not scraped (with snippets)
    queries: list[SearchQuery]         # All search queries executed
```

Each `UrlEntry` contains `url`, `title`, and `content`. Each `SearchQuery` contains `query`, `num_results_returned`, and `domains_restricted`.

## Requirements

- Python >= 3.10
- An API key for at least one LLM provider
- (Optional) `SERPER_API_KEY` for Google-quality search — or use DuckDuckGo for free

## License

MIT
