# Evaluation

Web Scout (v1.13.0, default settings) and OpenAI web search (`gpt-6-astra`, high reasoning effort, built-in `web_search` tool) answered the same 11 questions on 2026-10-27, twice each. Eight are document-heavy questions whose answers sit inside official documents, data portals and PDFs. Three are simple lookups that a search snippet can answer.

## How answers are scored

- An independent fetcher, sharing no code with Web Scout, downloads every page each answer cites. Pages that only render with JavaScript are opened in headless Chromium.
- A judge (`gemini/gemini-3.1-pro-preview`) splits each answer into factual claims and checks each one against the text of the pages that answer cites. A claim counts as supported when the information is in those pages, even if its link points at a neighbouring source.
- **Verified claims** is the share of all claims that cite a page and are supported by the cited pages. Uncited and unsupported claims count against it. Claims whose cited page the fetcher could not read are left out.
- **Completeness** (1–5) is scored without the system's name.
- Only the final answers are scored. Web Scout also returns the extract it took from every source it read (`result.scraped`); OpenAI web search returns links and titles only, with no source text, so there is nothing to compare there.
- There is no overall "which answer is better" vote: that kind of judgment mostly rewards writing style, not whether the claims hold up.

## Results

Cells are Web Scout / OpenAI web search, averaged over two runs. — means no claim could be checked because the cited pages could not be fetched.

| Question | Kind | Verified claims | Unsupported per answer | Claims with a citation | Completeness (1–5) |
| --- | --- | --- | --- | --- | --- |
| Guatemala hotspots | document-heavy | 91% / 90% | 1.0 / 2.5 | 100% / 100% | 5.0 / 5.0 |
| Brazil, Legal Amazon | document-heavy | 67% / 50% | 1.0 / 1.0 | 94% / 54% | 4.5 / 3.0 |
| Senegal small pelagics | document-heavy | 55% / 52% | 2.5 / 2.0 | 100% / 69% | 5.0 / 3.5 |
| Raja Ampat rules | document-heavy | 98% / 97% | 0.5 / 0.5 | 100% / 100% | 3.5 / 5.0 |
| Peru anchoveta quota | document-heavy | 100% / 100% | 0.0 / 0.0 | 100% / 100% | 5.0 / 5.0 |
| EUDR country benchmarking | document-heavy | 100% / 88% | 0.0 / 1.0 | 100% / 96% | 4.5 / 5.0 |
| Kenya maize statistics | document-heavy | 100% / — | 0.0 / 0.0 | 100% / 100% | 3.0 / 3.0 |
| Côte d'Ivoire forest inventory | document-heavy | 95% / 83% | 0.5 / 2.0 | 100% / 100% | 5.0 / 5.0 |
| FAO Director-General | simple lookup | 100% / 100% | 0.0 / 0.0 | 100% / 100% | 5.0 / 5.0 |
| IFAD headquarters | simple lookup | 100% / — | 0.0 / 0.0 | 100% / 100% | 5.0 / 5.0 |
| FAO Food Price Index | simple lookup | 100% / 100% | 0.0 / 0.0 | 100% / 100% | 5.0 / 5.0 |
| **Document-heavy questions** |  | 91% / 78% | 0.7 / 1.1 | 99% / 87% | 4.4 / 4.3 |
| **Simple lookups** |  | 100% / 100% | 0.0 / 0.0 | 100% / 100% | 5.0 / 5.0 |

## Limitations

Eleven questions is a small set, and the judge is a language model. A few cited pages could not be read by the fetcher (blocked sites, raw data files), so claims resting on them are excluded rather than scored. Figures Web Scout reads from charts are checked against the page text only, so they can count as unsupported.

## Reproduce

```bash
python eval/compare_search.py                                       # run both systems and judge
python eval/compare_search.py --judge-dir eval/results/<run-date>   # re-score saved answers
```

The script needs `OPENAI_API_KEY`, `GEMINI_API_KEY` and Web Scout's own keys. Questions are in [`eval/questions.json`](https://github.com/RSO9192/web-scout-ai/blob/main/eval/questions.json).
