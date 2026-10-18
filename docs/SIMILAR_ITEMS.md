# Similar items

An add-on to the core app. The item page shows a small **Similar items** panel listing other items whose title and
description share words with the one you're looking at, so people spot duplicates and see how a problem was handled
before. It needs no AI service, no API key and no new container: it is plain PostgreSQL full-text search.

## How it works

1. **The words are already indexed.** `work_items.search_tsv` is a generated column holding the item's title and
   description as a `tsvector`: lower-cased, stemmed words with stop words removed ("Customer was charged twice" becomes
   `'charg' 'custom' 'twice'`). It has a GIN index (`ix_items_search`) and is what the list page's search box uses.
   So this feature needs no migration.
2. **The item becomes a query.** `GET /api/items/{id}/similar` reads the item's own words and joins them with OR:
   `'charg' | 'custom' | 'twice'`. Any other item sharing at least one word matches, using the GIN index.
3. **Best match first.** Matches are sorted by `ts_rank`, which scores higher the more of those words an item contains.
   Ties go to the newest item. The default is 5 results (`?limit=` up to 20).
4. **Same permissions as everything else.** The source item is loaded with `load_item()`, so someone outside its team
   gets 404. Results go through the same `_visible()` team filter as the item list, so you only ever see items from
   your own teams.

The code is one function, `similar_items()` in `backend/app/items.py`, one route in `backend/app/api/routes.py`, and
`frontend/src/components/SimilarItems.tsx`. Tests are in `backend/tests/test_similar.py` and the last two tests of
`frontend/src/__tests__/item-page.test.tsx`.

## Limits

- It matches **words, not meaning**. "Can't sign in" and "login fails" share no words, so they won't match.
- Very common words in your data (for example "customer") make many items match; `ts_rank` still puts the closest ones
  first. On the 20,000-item seed data a lookup takes about 100 ms.

## Moving to embeddings later

If matching by meaning becomes worth it, the endpoint and the panel stay exactly the same; only `similar_items()`
changes:

1. Add the `pgvector` extension and an `embedding vector(384)` column on `work_items`, with an HNSW index.
2. Compute the embedding from title + description with a small local model (for example a sentence-transformers model
   running in the worker), so there's still no external API. The worker already processes the outbox, so an
   "item created / edited" outbox event can trigger re-embedding without slowing down the request.
3. Order by cosine distance (`embedding <=> :item_embedding`) instead of `ts_rank`, keeping the same team filter.
   A good middle step is a hybrid: take the top full-text and top vector matches and merge them.
