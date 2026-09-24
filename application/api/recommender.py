"""Recommendation service: the strategies, the automatic strategy selection, and the
translation of engine output (internal integer ids) into the public vocabulary (item_id).

Three layers, kept apart so each is testable on its own:

  * strategy functions (rec_popular / rec_content / rec_hybrid / rec_collaborative /
    rec_session) - the engine's actual ranking logic, moved here unchanged in behavior from
    the route handlers that used to inline it. They work on internal ids and return plain
    record dicts.
  * plan_strategies() - a pure function: which strategies to try, in which order, given what
    signals the request carries and what data exists. No I/O.
  * recommend_auto() / present_records() - glue: resolve the public ids, run the plan until
    a strategy yields items, then translate the result back to public ids.
"""
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from exploreData import get_data, get_data_similarities, get_data_users_purchases, stopwords_terms  # noqa: E402
from modelData import (  # noqa: E402
    SEMANTIC_BLEND_WEIGHT, _model_path, build_user_item_matrix, compute_popularity_scores,
    get_cosine_similarities_cached, load_model, predict_items_from_user_api,
)
from db import (  # noqa: E402
    KIND_ITEM, KIND_USER, PURCHASE, count_user_signal_interactions, fetch_item_properties,
    find_similar_by_embedding, get_active_model_version, get_dominant_event_type,
    get_recent_viewed_work_ids, get_recent_viewed_work_ids_for_session, resolve_external_ids,
    resolve_internal_ids,
)
from ids import numeric_alias  # noqa: E402

# ---------------------------------------------------------------------------
# Engine: shared helpers
# ---------------------------------------------------------------------------


def normalize_scores(series: pd.Series) -> pd.Series:
    """Min-max normalize a score series to [0, 1] so two differently-scaled signals
    (content cosine similarity, ALS score) can be blended with a meaningful weight."""
    if series.empty:
        return series
    span = series.max() - series.min()
    if span == 0:
        return series * 0.0 + 1.0
    return (series - series.min()) / span


def diversify_by_genre(candidates: pd.DataFrame, allowed_genres: set, count: int, genre_first: bool = False) -> pd.DataFrame:
    """Guarantees at least half the results share a genre the visitor has shown interest
    in. Fixes a real failure mode: a single candidate can score anomalously high on both
    TF-IDF and semantic similarity purely from a coincidental shared phrase (e.g. "The Bad
    Guys 2" naming its rival gang "The Bad Girls" in-story, which text similarity reads as
    a strong match to an unrelated film literally titled "Bad Girls") and crowd out every
    genre-appropriate alternative.

    genre_first=False (default, used by content recs - the "Pourquoi ?"/Cold start
    pages): guarantees inclusion but re-ranks the union by raw score, so a genuinely
    dominant cross-genre match (Jurassic Park for Jurassic World Rebirth, Avengers sequels
    for The Avengers) still wins the top spot on merit.

    genre_first=True (used by session recs - "Vous aimerez aussi" on the homepage/
    product pages): genre-matched candidates are placed ahead of cross-genre ones
    regardless of raw score. Verified empirically that no score-based reweighting can
    separate a spurious cross-genre match (Bad Girls) from a genuine one (Jurassic Park) -
    both dominate their pool by a comparable margin on every available signal - so fixing
    one via score alone would have silently broken the other. This trades away Jurassic
    Park's top spot in this specific strategy to guarantee Bad Guys 2 surfaces real family
    films first; the "Pourquoi ?" page keeps the other behavior."""
    if not allowed_genres:
        return candidates.sort_values('score', ascending=False).head(count)

    ranked = candidates.sort_values('score', ascending=False)
    quota = math.ceil(count / 2)
    same_genre = ranked[ranked['genre_1'].isin(allowed_genres)].head(quota)
    remaining = count - len(same_genre)
    others = ranked[~ranked['work_id'].isin(same_genre['work_id'])].head(remaining)
    if genre_first:
        return pd.concat([same_genre, others])
    return pd.concat([same_genre, others]).sort_values('score', ascending=False)


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient='records', date_format='iso'))


def popularity_label(client_id: int) -> str:
    """Plural-ish label for the popularity explanation text - "achats" for the default
    purchase type (matches the exact pre-generalization copy), the tenant's own label
    lowercased for any other dominant (highest-tier) event type."""
    dominant = get_dominant_event_type(client_id)
    if dominant is None or dominant["event_type"] == PURCHASE:
        return "achats"
    return dominant["label"].lower()


def _catalog_similarity(client_id: int, product_type: str):
    """(catalog, cosine matrix, work_id -> matrix row). The matrix rows are positional in
    `catalog` - get_data_similarities never drops rows precisely so that holds."""
    data_works = get_data(product_type, product_id=None, count=None, client_id=client_id)
    data_similarities = get_data_similarities(data_works)
    cosine_sim, _ = get_cosine_similarities_cached(
        data_works, data_similarities['bag_of_words'], stopwords_terms, "Tfidf", client_id, product_type
    )
    work_id_to_idx = dict(zip(data_works['work_id'], range(len(data_works))))
    return data_works, cosine_sim, work_id_to_idx


def _top_similar(cosine_row, catalog: pd.DataFrame, anchor_idx: int, limit: int) -> pd.DataFrame:
    """The `limit` catalog rows most cosine-similar to the anchor, anchor excluded, with the
    similarity attached (previously computed but discarded, so callers had no way to
    explain the ranking). Tie order matches the original title-indexed implementation."""
    ranked = sorted(enumerate(cosine_row), key=lambda pair: pair[1], reverse=True)
    picked = [(i, s) for i, s in ranked if i != anchor_idx][:limit]
    rec = catalog.iloc[[i for i, _ in picked]].copy()
    rec['content_similarity'] = [s for _, s in picked]
    return rec


# ---------------------------------------------------------------------------
# Engine: strategies (internal ids in, internal-id records out)
# ---------------------------------------------------------------------------

def rec_popular(
    client_id: int, product_type: str, count: int,
    exclude_work_ids: Optional[set] = None, catalog_fallback: bool = False,
) -> list[dict]:
    """Pure popularity ranking, no anchor product or user history needed - the true
    cold-start fallback. With catalog_fallback, the list is topped up with the catalog's own
    first items when fewer than `count` items have any signal-carrying interactions yet - so a
    brand-new integration that has upserted items but sent few or no events still gets a full
    answer instead of an empty list."""
    exclude_work_ids = exclude_work_ids or set()
    data_works = get_data(product_type, product_id=None, count=None, client_id=client_id)
    popularity = compute_popularity_scores(product_type, client_id=client_id)

    records: list[dict] = []
    if not popularity.empty:
        merged = data_works.merge(popularity, on='work_id', how='inner')
        merged = merged[~merged['work_id'].isin(exclude_work_ids)]
        merged = merged.sort_values('popularity_score', ascending=False).head(count)

        label = popularity_label(client_id)
        records = _records(merged)
        for record in records:
            interaction_count = record.get('interaction_count')
            interaction_count = int(interaction_count) if interaction_count is not None else None
            record['score'] = record.get('popularity_score')
            record['explanation'] = {
                "reason": f"Populaire ({interaction_count} {label})" if interaction_count else "Recommandation populaire",
                "popularity_score": record.get('popularity_score'),
                "purchase_count": interaction_count if label == "achats" else None,
                "interaction_count": interaction_count,
                "interaction_label": label,
            }

    if catalog_fallback and len(records) < count and not data_works.empty:
        taken = exclude_work_ids | {int(r['work_id']) for r in records}
        filler = data_works[~data_works['work_id'].isin(taken)].head(count - len(records))
        for record in _records(filler):
            record['score'] = None
            record['explanation'] = {"reason": "Sélection du catalogue (pas encore assez d'interactions enregistrées)"}
            records.append(record)
    return records


def rec_content(client_id: int, product_type: str, work_id: int, count: int) -> list[dict]:
    """Content-based: TF-IDF cosine similarity blended with pgvector semantic neighbors.
    [] if the anchor isn't in the catalog."""
    data_works, cosine_sim, work_id_to_idx = _catalog_similarity(client_id, product_type)
    if work_id not in work_id_to_idx:
        return []
    anchor_idx = work_id_to_idx[work_id]
    title = data_works.iloc[anchor_idx]['title']

    # Wide TF-IDF candidate pool, blended below with semantic embedding neighbors -
    # TF-IDF alone only matches shared vocabulary; embeddings also catch paraphrased/
    # thematically similar synopses that share no literal words.
    pool_size = min(len(data_works), count * 5 + 1)
    tfidf_candidates = _top_similar(cosine_sim[anchor_idx], data_works, anchor_idx, limit=pool_size - 1)

    semantic_neighbors = find_similar_by_embedding(client_id, product_type, work_id, count=pool_size - 1)
    semantic_map = {n["work_id"]: n["semantic_similarity"] for n in semantic_neighbors}

    # Union of both candidate sets, so a strong semantic-only match isn't dropped just
    # because it fell outside the narrower TF-IDF pool, and vice versa.
    candidate_ids = set(tfidf_candidates["work_id"].tolist()) | set(semantic_map.keys())
    candidates = data_works[data_works["work_id"].isin(candidate_ids)].copy()
    if candidates.empty:
        return []

    tfidf_map = dict(zip(tfidf_candidates["work_id"], tfidf_candidates["content_similarity"]))
    candidates["content_similarity"] = candidates["work_id"].map(tfidf_map).fillna(0.0)
    candidates["semantic_similarity"] = candidates["work_id"].map(semantic_map).fillna(0.0)

    tfidf_norm = normalize_scores(candidates["content_similarity"])
    semantic_norm = normalize_scores(candidates["semantic_similarity"])
    candidates["score"] = (1 - SEMANTIC_BLEND_WEIGHT) * tfidf_norm + SEMANTIC_BLEND_WEIGHT * semantic_norm
    source_genre = data_works.iloc[anchor_idx]['genre_1']
    candidates = diversify_by_genre(candidates, {source_genre} if source_genre else set(), count)

    popularity = compute_popularity_scores(product_type, client_id=client_id)
    merged = candidates.merge(popularity, on='work_id', how='left')

    label = popularity_label(client_id)
    records = _records(merged)
    for record in records:
        interaction_count = record.get('interaction_count')
        interaction_count = int(interaction_count) if interaction_count is not None else None
        record['explanation'] = {
            "reason": (
                f"Similaire à « {title} » par le contenu (texte + similarité sémantique)"
                + (f", populaire ({interaction_count} {label})" if interaction_count else "")
            ),
            "content_similarity": record.get('content_similarity'),
            "semantic_similarity": record.get('semantic_similarity'),
            "popularity_score": record.get('popularity_score'),
            "purchase_count": interaction_count if label == "achats" else None,
            "interaction_count": interaction_count,
            "interaction_label": label,
        }
    return records


def rec_hybrid(client_id: int, product_type: str, user_id: Optional[int], work_id: int, count: int, alpha: float = 0.5) -> list[dict]:
    """Content candidates for the anchor, re-ranked by blending in the collaborative signal:
    score = alpha * collaborative + (1 - alpha) * content. Degrades to pure content ranking
    when there's no trained model or the user is unknown to it. [] if the anchor isn't in the
    catalog."""
    data_works, cosine_sim, work_id_to_idx = _catalog_similarity(client_id, product_type)
    if work_id not in work_id_to_idx:
        return []
    anchor_idx = work_id_to_idx[work_id]
    title = data_works.iloc[anchor_idx]['title']

    # Wide content-based candidate pool, then re-ranked by blending in the collaborative signal
    pool_size = min(len(data_works), count * 5 + 1)
    candidates = _top_similar(cosine_sim[anchor_idx], data_works, anchor_idx, limit=pool_size - 1)
    if candidates.empty:
        return []

    collab_scores = {}
    try:
        if user_id is None:
            raise ValueError("unknown user - nothing collaborative to blend in")
        rec_model = load_model(product_type, client_id=client_id)
        interactions = build_user_item_matrix(product_type, client_id=client_id)
        user_items_row = interactions[user_id] if user_id < interactions.shape[0] else None
        candidate_ids = candidates['work_id'].to_numpy()
        item_ids, scores = rec_model.recommend(
            user_id, user_items_row, N=len(candidate_ids),
            filter_already_liked_items=False, items=candidate_ids,
        )
        collab_scores = dict(zip(item_ids, scores))
    except (FileNotFoundError, IndexError, ValueError):
        pass  # no trained model yet, or a user it has never seen - degrade gracefully to pure content-based ranking

    content_norm = normalize_scores(candidates.set_index('work_id')['content_similarity'])
    collab_norm = normalize_scores(pd.Series(collab_scores, dtype=float)) if collab_scores else pd.Series(dtype=float)

    rows = []
    for _, row in candidates.iterrows():
        row_work_id = row['work_id']
        c_score = float(content_norm.get(row_work_id, 0.0))
        cf_score = float(collab_norm.get(row_work_id, 0.0))
        record = row.to_dict()
        record['score'] = alpha * cf_score + (1 - alpha) * c_score
        record['explanation'] = {
            "reason": f"Hybride : {alpha:.0%} collaboratif + {1 - alpha:.0%} contenu (similaire à « {title} »)",
            "content_similarity": c_score,
            "collaborative_score": cf_score if collab_scores else None,
        }
        rows.append(record)

    rows.sort(key=lambda r: r['score'], reverse=True)
    return _records(pd.DataFrame(rows[:count]))


def rec_collaborative(client_id: int, product_type: str, user_id: int, count: int) -> list[dict]:
    """User-based collaborative filtering (implicit ALS). Raises FileNotFoundError when no
    model has been trained yet - the explicit endpoint reports that, the automatic one
    falls back."""
    data_works = get_data(product_type, product_id=None, count=None, client_id=client_id)
    # Works already bought by the user are excluded - no point recommending them again
    data_purchases = get_data_users_purchases(product_type, user_id=None, count=None, client_id=client_id)
    return predict_items_from_user_api(product_type, data_works, data_purchases, user_id, count, client_id=client_id)


def rec_session(client_id: int, product_type: str, viewed_ids: list, count: int) -> list[dict]:
    """Recency-weighted content recs from a list of recently viewed work_ids (most recent
    last) - shared by the stateless client-supplied-list variant and the variants whose list
    comes from persisted history, so every personalization path ranks exactly the same way."""
    data_works, cosine_sim, work_id_to_idx = _catalog_similarity(client_id, product_type)
    valid_viewed_ids = [wid for wid in viewed_ids if wid in work_id_to_idx]
    if not valid_viewed_ids:
        return []

    # Recency weighting: the most recently viewed item (last in the list) counts most
    n = len(valid_viewed_ids)
    weights = [(i + 1) / n for i in range(n)]

    combined_scores = np.zeros(cosine_sim.shape[0])
    for work_id, weight in zip(valid_viewed_ids, weights):
        combined_scores += weight * cosine_sim[work_id_to_idx[work_id]]

    viewed_idx_set = {work_id_to_idx[wid] for wid in valid_viewed_ids}
    order = np.argsort(combined_scores)[::-1]

    # Genres the visitor has shown interest in across everything viewed so far - used
    # below to keep one anomalously-scoring cross-genre match from crowding out every
    # genre-appropriate alternative (see diversify_by_genre).
    viewed_genres = set(data_works.loc[data_works['work_id'].isin(valid_viewed_ids), 'genre_1'].dropna())

    pool_size = min(len(data_works), count * 5 + 1)
    pool_rows = []
    for idx in order:
        if idx in viewed_idx_set:
            continue

        # Which viewed item most drove this particular recommendation
        best_source_wid, best_sim = None, -1.0
        for source_wid in valid_viewed_ids:
            sim = cosine_sim[work_id_to_idx[source_wid], idx]
            if sim > best_sim:
                best_sim, best_source_wid = sim, source_wid
        source_title = data_works.loc[data_works['work_id'] == best_source_wid, 'title'].iloc[0]

        row = data_works.iloc[idx].to_dict()
        row['score'] = float(combined_scores[idx])
        row['explanation'] = {
            "reason": f"Similaire à « {source_title} », consulté récemment",
            "content_similarity": float(best_sim),
            "source_work_ids": valid_viewed_ids,
        }
        pool_rows.append(row)
        if len(pool_rows) >= pool_size:
            break

    if not pool_rows:
        return []

    final_df = diversify_by_genre(pd.DataFrame(pool_rows), viewed_genres, count, genre_first=True)
    return _records(final_df)


def model_available(client_id: int, product_type: str) -> bool:
    """Is there a trained collaborative model on disk to serve from? Mirrors load_model's
    lookup: the active tracked version, else the legacy fixed path."""
    active = get_active_model_version(client_id, product_type)
    path = active["file_path"] if active is not None else _model_path(product_type, client_id)
    return os.path.exists(path)


# ---------------------------------------------------------------------------
# Automatic strategy selection
# ---------------------------------------------------------------------------

HYBRID = "hybrid"
CONTENT = "content"
COLLABORATIVE = "collaborative"
SESSION = "session"                  # from the viewed_item_ids the caller sent
SESSION_HISTORY = "session_history"  # from views LIKYLY persisted for this user / session
POPULAR = "popular"


def public_strategy_name(step: str) -> str:
    """Both session variants are the same strategy to the outside world."""
    return SESSION if step == SESSION_HISTORY else step


def plan_strategies(
    *, has_user: bool, has_item: bool, has_viewed: bool, has_history: bool,
    user_has_signal: bool, model_available: bool,
) -> list[str]:
    """Ordered list of strategies to try; the first one that yields items wins, and
    "popular" is always last so a request never ends up with nothing to try.

        user + item                -> hybrid
        item                       -> content
        viewed_item_ids            -> session
        user with history + model  -> collaborative
        user / session with views  -> session (from persisted history)
        anything else              -> popular

    Every step is included only when the signal it needs exists - so the plan for "user +
    item" is [hybrid, content, ..., popular]: hybrid is preferred, and if for whatever reason
    it can't produce anything the same request degrades to plain content rather than failing.
    """
    plan: list[str] = []
    if has_user and has_item:
        plan.append(HYBRID)
    if has_item:
        plan.append(CONTENT)
    if has_viewed:
        plan.append(SESSION)
    if has_user and user_has_signal and model_available:
        plan.append(COLLABORATIVE)
    if has_history:
        plan.append(SESSION_HISTORY)
    plan.append(POPULAR)
    return plan


@dataclass
class RecommendationResult:
    strategy: str
    records: list[dict]
    # Every step tried, in order, ending with the one that produced `records` - for logs.
    attempted: list[str] = field(default_factory=list)


def recommend_auto(
    client_id: int, product_type: str, *, user_id: Optional[str], session_id: Optional[str],
    item_id: Optional[str], viewed_item_ids: list[str], count: int,
) -> RecommendationResult:
    """Resolve the caller's ids (read-only: nothing is created for an id we've never seen),
    plan, then run the plan. An id LIKYLY doesn't know (an item not in the catalog, a user
    with no events yet) simply doesn't contribute a signal - it never turns into an error."""
    internal_user = resolve_internal_ids(client_id, product_type, KIND_USER, [user_id]).get(user_id) if user_id else None
    catalog_ids = set(get_data(product_type, None, None, client_id=client_id)['work_id'].tolist())

    anchor = None
    if item_id:
        candidate = resolve_internal_ids(client_id, product_type, KIND_ITEM, [item_id]).get(item_id)
        anchor = candidate if candidate in catalog_ids else None

    viewed: list[int] = []
    if viewed_item_ids:
        mapping = resolve_internal_ids(client_id, product_type, KIND_ITEM, viewed_item_ids)
        viewed = [mapping[v] for v in viewed_item_ids if v in mapping and mapping[v] in catalog_ids]

    history: list[int] = []
    if not viewed:
        if internal_user is not None:
            history = get_recent_viewed_work_ids(client_id, product_type, internal_user, limit=10)
        if not history and session_id:
            history = get_recent_viewed_work_ids_for_session(client_id, product_type, session_id, limit=10)

    user_has_signal = internal_user is not None and count_user_signal_interactions(client_id, product_type, internal_user) > 0
    plan = plan_strategies(
        has_user=internal_user is not None, has_item=anchor is not None, has_viewed=bool(viewed),
        has_history=bool(history), user_has_signal=user_has_signal,
        model_available=model_available(client_id, product_type) if user_has_signal else False,
    )

    exclude = set(viewed) | ({anchor} if anchor is not None else set())
    attempted: list[str] = []
    for step in plan:
        attempted.append(step)
        records = _run_step(
            step, client_id, product_type, count, user=internal_user, anchor=anchor,
            viewed=viewed, history=history, exclude=exclude,
        )
        if records:
            return RecommendationResult(public_strategy_name(step), records, attempted)
    return RecommendationResult(POPULAR, [], attempted)


def _run_step(step, client_id, product_type, count, *, user, anchor, viewed, history, exclude) -> list[dict]:
    if step == HYBRID:
        return rec_hybrid(client_id, product_type, user, anchor, count)
    if step == CONTENT:
        return rec_content(client_id, product_type, anchor, count)
    if step == SESSION:
        return rec_session(client_id, product_type, viewed, count)
    if step == COLLABORATIVE:
        try:
            return rec_collaborative(client_id, product_type, user, count)
        except (FileNotFoundError, IndexError, ValueError):
            return []  # model vanished / user unknown to it - next step
    if step == SESSION_HISTORY:
        return rec_session(client_id, product_type, history, count)
    return rec_popular(client_id, product_type, count, exclude_work_ids=exclude, catalog_fallback=True)


# ---------------------------------------------------------------------------
# Presentation: internal records -> public vocabulary
# ---------------------------------------------------------------------------

def _stored_or_synthesized_properties(record: dict, stored: Optional[dict]) -> dict:
    """The item's `properties`: what the integrator sent, or - for a row written before
    that column existed - rebuilt from the structured columns."""
    if stored is not None:
        return stored
    year = record.get('year')
    synthesized = {
        "category": record.get('genre_1'),
        "author": record.get('author'),
        "year": int(year) if isinstance(year, (int, float)) and not pd.isna(year) else None,
        "url": record.get('url'),
        "price": record.get('price'),
    }
    return {k: v for k, v in synthesized.items() if v is not None}


def item_properties_for(record: dict, stored: Optional[dict]) -> dict:
    return _stored_or_synthesized_properties(record, stored)


def present_records(client_id: int, product_type: str, records: list[dict], *, legacy: bool, include_similar_users: bool = False) -> list[dict]:
    """Translate engine records to public ones. `legacy=True` produces the historical
    RecommendedProduct shape (adds the deprecated work_id / genre_1 / ... fields, keeps
    purchase_count and source_work_ids); `legacy=False` the clean RecommendedItem shape.
    `similar_users` (other users' names) is only passed through when include_similar_users -
    the caller decides who may see it."""
    if not records:
        return []

    work_ids = {int(r['work_id']) for r in records}
    user_ids: set[int] = set()
    for r in records:
        explanation = r.get('explanation') or {}
        work_ids.update(int(w) for w in explanation.get('source_work_ids') or [])
        for su in explanation.get('similar_users') or []:
            user_ids.add(int(su['user_id']))
            work_ids.update(int(w) for w in su.get('shared_work_ids') or [])

    external_items = resolve_external_ids(client_id, product_type, KIND_ITEM, list(work_ids))
    external_users = resolve_external_ids(client_id, product_type, KIND_USER, list(user_ids))
    stored_properties = fetch_item_properties(client_id, product_type, [int(r['work_id']) for r in records])

    def item_ext(work_id) -> str:
        return external_items.get(int(work_id), str(int(work_id)))

    def user_ext(user_id) -> str:
        return external_users.get(int(user_id), str(int(user_id)))

    presented = []
    for record in records:
        work_id = int(record['work_id'])
        external_id = item_ext(work_id)
        properties = item_properties_for(record, stored_properties.get(work_id))
        explanation = _present_explanation(record.get('explanation'), item_ext, user_ext, legacy, include_similar_users)

        if legacy:
            presented.append({
                "item_id": external_id, "work_id": numeric_alias(external_id),
                "title": record.get('title'), "description": record.get('description'),
                "genre_1": record.get('genre_1'), "author": record.get('author'),
                "year": record.get('year'), "url": record.get('url'), "price": record.get('price'),
                "properties": properties, "score": record.get('score'), "explanation": explanation,
            })
        else:
            presented.append({
                "item_id": external_id, "score": record.get('score'), "title": record.get('title'),
                "description": record.get('description'), "properties": properties,
                "explanation": explanation,
            })
    return presented


def _present_explanation(explanation: Optional[dict], item_ext, user_ext, legacy: bool, include_similar_users: bool) -> Optional[dict]:
    if not explanation:
        return None
    out = {k: v for k, v in explanation.items() if k not in ('source_work_ids', 'similar_users')}
    if not legacy:
        out.pop('purchase_count', None)

    source_work_ids = explanation.get('source_work_ids')
    if source_work_ids is not None:
        source_item_ids = [item_ext(w) for w in source_work_ids]
        out['source_item_ids'] = source_item_ids
        if legacy:
            out['source_work_ids'] = [a for a in (numeric_alias(e) for e in source_item_ids) if a is not None]

    similar_users = explanation.get('similar_users')
    if similar_users and include_similar_users:
        presented_users = []
        for su in similar_users:
            shared = [item_ext(w) for w in su.get('shared_work_ids') or []]
            if legacy:
                presented_users.append({
                    "user_id": user_ext(su['user_id']), "name": su['name'], "shared_item_ids": shared,
                    "shared_work_ids": [a for a in (numeric_alias(e) for e in shared) if a is not None],
                })
            else:
                presented_users.append({"user_id": user_ext(su['user_id']), "shared_item_ids": shared})
        out['similar_users'] = presented_users
    return out
