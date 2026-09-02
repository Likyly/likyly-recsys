from implicit.cpu.als import AlternatingLeastSquares
from implicit.evaluation import train_test_split as als_train_test_split, precision_at_k
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from pinecone import Pinecone, ServerlessSpec
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from datetime import datetime
from scipy.sparse import csr_matrix
import torch
import numpy as np
import pandas as pd
import re
import subprocess
import time
import json

import sys
import os

from exploreData import get_data_users_purchases, get_data_users_page_views, get_data_users
from db import (
    DEMO_CLIENT_ID, MANUAL, record_model_version, update_model_version_file_path,
    get_active_model_version, promote_model_version,
    update_product_embedding, find_similar_by_embedding,
    fetch_all_interactions, get_client_event_type_weights, EVENT_TIER_WEIGHTS, DEFAULT_EVENT_TIER,
)

import mlflow
from mlflow.tracking import MlflowClient

# MLflow is a visibility/governance layer on top of the model_versions table + .npz files,
# which stay the actual source of truth for serving (load_model never depends on MLflow).
# A logging failure here (server down, network hiccup) must never block a real training
# run - see the try/except around _log_training_run_to_mlflow below.
mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5555"))

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Confidence weighting for implicit feedback: each of a tenant's registered event types
# (purchase, view, or any custom type - see EVENT_TIER_WEIGHTS/get_client_event_type_weights
# in db.py) has its own weight, applied per-row in build_user_item_matrix below.
ALS_FACTORS = 64
ALS_REGULARIZATION = 0.01
ALS_ITERATIONS = 20

# How much weight the semantic embedding signal gets versus TF-IDF when blending content
# scores in get_rec_content - see app.py. Equal weight by default: TF-IDF stays valuable
# for its explainability (literal shared vocabulary), embeddings add genuine paraphrase/
# semantic matching TF-IDF structurally can't do - neither fully replaces the other yet.
SEMANTIC_BLEND_WEIGHT = 0.5

_SENTENCE_TRANSFORMER = None


def _get_cached_sentence_transformer():
    # Loading this model from disk takes a real 1-3s - caching it once per process avoids
    # paying that cost on every single product upsert or recommendation request.
    global _SENTENCE_TRANSFORMER
    if _SENTENCE_TRANSFORMER is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        _SENTENCE_TRANSFORMER = SentenceTransformer('sentence-transformers/all-MiniLM-L12-v2').to(device)
    return _SENTENCE_TRANSFORMER


def compute_embedding(text: str) -> list:
    model = _get_cached_sentence_transformer()
    return model.encode(text).tolist()


def compute_and_store_product_embedding(client_id, product_type, work_id, title, description=None, genre_1=None):
    text = " ".join(filter(None, [title, description, genre_1]))
    embedding = compute_embedding(text)
    update_product_embedding(client_id, product_type, work_id, embedding)
    return embedding


def dvc_push(file_path=None):

    try:
        if file_path:
            # Add and Push a specific file
            subprocess.run(['dvc', 'add', file_path], check=True)
            subprocess.run(['dvc', 'push', file_path], check=True)
        else:
            # Add and Push all DVC-tracked files
            subprocess.run(['dvc', 'add'], check=True)
            subprocess.run(['dvc', 'push'], check=True)
        print("DVC push successful.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while pushing with DVC: {e}", file=sys.stderr)


def build_user_item_matrix(product_type, client_id=DEMO_CLIENT_ID):
    """Sparse (user_id x work_id) confidence matrix built from every interaction type
    this client has registered, each weighted by its own tier (see
    get_client_event_type_weights in db.py) - no fabricated 1-5 rating. This is what
    implicit's ALS trains and predicts on. purchase/view keep their original 3.0/0.2
    weights by default (seeded for every client - see seed_default_event_types), so this
    produces the same output as before this was generalized beyond those two types."""
    interactions = fetch_all_interactions(product_type, client_id=client_id)
    if interactions.empty:
        return csr_matrix((1, 1), dtype=np.float32)

    weights = get_client_event_type_weights(client_id)
    default_weight = EVENT_TIER_WEIGHTS[DEFAULT_EVENT_TIER]
    interactions = interactions.copy()
    interactions['weight'] = interactions['quantity'] * interactions['event_type'].map(
        lambda et: weights.get(et, default_weight)
    )

    confidence = interactions.groupby(['user_id', 'work_id'], as_index=False)['weight'].sum()

    max_user_id = int(confidence['user_id'].max())
    max_work_id = int(confidence['work_id'].max())

    return csr_matrix(
        (confidence['weight'].astype(np.float32),
         (confidence['user_id'].astype(int), confidence['work_id'].astype(int))),
        shape=(max_user_id + 1, max_work_id + 1),
    )


def create_model(interactions_matrix, factors=ALS_FACTORS, regularization=ALS_REGULARIZATION, iterations=ALS_ITERATIONS):
    model = AlternatingLeastSquares(
        factors=factors, regularization=regularization, iterations=iterations, random_state=42
    )
    model.fit(interactions_matrix)
    return model


def evaluate_model(interactions_matrix, factors=ALS_FACTORS, regularization=ALS_REGULARIZATION, iterations=ALS_ITERATIONS, k=10):
    """Held-out precision@k - the implicit-feedback analog of the RMSE evaluation the
    previous explicit Spotlight model used, since RMSE doesn't apply to ranking models."""
    train, test = als_train_test_split(interactions_matrix, train_percentage=0.8, random_state=42)
    model = AlternatingLeastSquares(
        factors=factors, regularization=regularization, iterations=iterations, random_state=42
    )
    model.fit(train)
    precision = precision_at_k(model, train, test, K=k, show_progress=False)
    return model, precision


def _model_path(data_work_type, client_id):
    # Legacy fixed path used before per-version tracking existed. Kept only as a
    # one-time fallback in load_model, for models trained before this migration.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    relative_path_model = f"../../model/client_{client_id}/{data_work_type}_users_rating_model.npz"
    return os.path.abspath(os.path.join(current_dir, relative_path_model))


def _versioned_model_path(data_work_type, client_id, version_id):
    # Namespaced by client (so two clients can't collide) and suffixed by version id (so
    # a new training run never overwrites a previous artifact still referenced by the
    # model_versions table - a rollback needs the old file to still exist on disk).
    current_dir = os.path.dirname(os.path.abspath(__file__))
    relative_path_model = f"../../model/client_{client_id}/{data_work_type}_users_rating_model_v{version_id}.npz"
    return os.path.abspath(os.path.join(current_dir, relative_path_model))


def load_model(data_work_type, client_id=DEMO_CLIENT_ID):

    active = get_active_model_version(client_id, data_work_type)
    if active is not None:
        absolute_path_model = active["file_path"]
    else:
        # No tracked version yet - fall back to a model trained before version tracking
        # was introduced, so this change doesn't break anything already being served.
        absolute_path_model = _model_path(data_work_type, client_id)

    if not os.path.exists(absolute_path_model):
        raise FileNotFoundError(f"No trained model found for '{data_work_type}' at {absolute_path_model}")

    return AlternatingLeastSquares.load(absolute_path_model)


def train_and_maybe_promote_model(
    product_type,
    client_id=DEMO_CLIENT_ID,
    factors=ALS_FACTORS,
    regularization=ALS_REGULARIZATION,
    iterations=ALS_ITERATIONS,
    k=10,
    triggered_by=MANUAL,
):
    """Trains a new ALS model, evaluates it against a held-out split, and only promotes
    it to be the one actually served if it's at least as good as the currently active
    version - so a bad retrain (e.g. after a burst of noisy interactions) never silently
    degrades production. Every run is recorded regardless of outcome, so a rejected
    candidate stays visible in the version history instead of disappearing."""
    interactions = build_user_item_matrix(product_type, client_id=client_id)

    _, candidate_precision = evaluate_model(
        interactions, factors=factors, regularization=regularization, iterations=iterations, k=k,
    )

    # The held-out split above is only for an honest precision@k figure - the artifact we
    # actually serve is trained on 100% of the available data.
    final_model = create_model(interactions, factors=factors, regularization=regularization, iterations=iterations)

    active = get_active_model_version(client_id, product_type)
    is_improvement = (
        active is None
        or active["precision_at_k"] is None
        or (candidate_precision is not None and candidate_precision >= active["precision_at_k"])
    )

    version_id = record_model_version(
        client_id=client_id, product_type=product_type, file_path="pending",
        factors=factors, regularization=regularization, iterations=iterations,
        precision_at_k=candidate_precision,
        num_users=interactions.shape[0], num_items=interactions.shape[1],
        num_interactions=int(interactions.nnz),
        triggered_by=triggered_by,
    )

    file_path = _versioned_model_path(product_type, client_id, version_id)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    final_model.save(file_path)
    update_model_version_file_path(version_id, file_path)
    dvc_push(file_path)

    if is_improvement:
        promote_model_version(version_id, client_id, product_type)

    _log_training_run_to_mlflow(
        client_id=client_id, product_type=product_type, version_id=version_id, file_path=file_path,
        factors=factors, regularization=regularization, iterations=iterations,
        precision_at_k=candidate_precision, num_users=interactions.shape[0],
        num_items=interactions.shape[1], num_interactions=int(interactions.nnz),
        promoted=is_improvement,
    )

    return {
        "version_id": version_id,
        "precision_at_k": candidate_precision,
        "promoted": is_improvement,
        "previous_precision_at_k": active["precision_at_k"] if active else None,
    }


def _log_training_run_to_mlflow(
    client_id, product_type, version_id, file_path,
    factors, regularization, iterations, precision_at_k,
    num_users, num_items, num_interactions, promoted,
):
    """Logs this training run to MLflow for visual/historical inspection - params,
    metrics, the model artifact, and (if promoted) a Model Registry version transitioned
    to the Production stage, with the previous Production version auto-archived. This is
    purely a visibility layer: serving (load_model) never depends on MLflow, so a
    logging failure here (server down, network hiccup) must never block or fail the
    actual training/promotion decision already made and persisted in Postgres above."""
    try:
        registered_name = f"client_{client_id}_{product_type}"
        mlflow.set_experiment(registered_name)

        with mlflow.start_run(run_name=f"v{version_id}") as run:
            mlflow.log_params({
                "factors": factors, "regularization": regularization, "iterations": iterations,
                "version_id": version_id,
            })
            metrics = {"num_users": num_users, "num_items": num_items, "num_interactions": num_interactions}
            if precision_at_k is not None:
                metrics["precision_at_k"] = precision_at_k
            mlflow.log_metrics(metrics)
            mlflow.set_tag("promoted", str(promoted))
            mlflow.log_artifact(file_path)

            client = MlflowClient()
            try:
                client.create_registered_model(registered_name)
            except Exception:
                pass  # already exists, fine

            # await_creation_for=0: skip MLflow's post-creation polling loop, which hits a
            # broken query against this Postgres/psycopg setup (integer/varchar comparison
            # error) - the version is already created synchronously by the call above this
            # wait step, so there's nothing to actually wait for.
            model_version = client.create_model_version(
                name=registered_name,
                source=f"{run.info.artifact_uri}/{os.path.basename(file_path)}",
                run_id=run.info.run_id,
                await_creation_for=0,
            )
            if promoted:
                # Aliases (not the deprecated stage-transition API) are MLflow's current
                # recommended way to mark "the one in production" - reassigning the alias
                # is a single atomic pointer move, no separate archive step needed.
                client.set_registered_model_alias(registered_name, "production", model_version.version)
    except Exception as error:
        print(f"MLflow logging skipped (non-fatal): {error}")


def predict_items_from_user(model, data, user_id, count: int = 3, exclude_work_ids=None, user_items_row=None):
    # Restrict candidates to the given catalog slice (optionally excluding already-owned
    # items), then let ALS rank only within that subset instead of the whole catalog.
    candidate_ids = data['work_id'].to_numpy()
    if exclude_work_ids:
        candidate_ids = candidate_ids[~np.isin(candidate_ids, list(exclude_work_ids))]

    if len(candidate_ids) == 0:
        return data.iloc[0:0].assign(score=pd.Series(dtype=float))

    # recalculate_user=True re-solves this single user's latent factors on the fly from
    # their current interaction row, instead of using the factors frozen at last training
    # time - so a purchase/view made after training is reflected immediately, without
    # waiting for the next full retrain. Only safe when we actually have a row to
    # recalculate from; a user with zero interactions falls back to the trained factors.
    item_ids, scores = model.recommend(
        int(user_id), user_items_row, N=min(count, len(candidate_ids)),
        filter_already_liked_items=False, items=candidate_ids,
        recalculate_user=user_items_row is not None,
    )

    rec = data[data['work_id'].isin(item_ids)].copy()
    score_map = dict(zip(item_ids, scores))
    rec['score'] = rec['work_id'].map(score_map)

    return rec.sort_values(by='score', ascending=False)


def predict_items_from_user_api(product_type, data_works, data_purchases, user_id, count: int = 3, client_id=DEMO_CLIENT_ID):

    user_id = int(user_id)

    # We don't want to propose works that the user has already bought
    data_works_purchased_by_user = data_purchases[data_purchases['user_id'] == user_id]
    purchased_work_ids = (
        set(data_works_purchased_by_user['work_id'].unique())
        if not data_works_purchased_by_user.empty else set()
    )

    rec_model = load_model(product_type, client_id=client_id)
    interactions = build_user_item_matrix(product_type, client_id=client_id)
    user_items_row = interactions[user_id] if user_id < interactions.shape[0] else None

    rec_df_rating = predict_items_from_user(
        rec_model, data_works, user_id, count,
        exclude_work_ids=purchased_work_ids, user_items_row=user_items_row,
    )

    similar_users = find_similar_users(product_type, user_id, top_n=2, client_id=client_id)
    reason = "Recommandé par filtrage collaboratif : apprécié par des utilisateurs aux goûts proches"
    if similar_users:
        reason += " (dont " + ", ".join(su["name"] for su in similar_users) + ")"

    records = json.loads(rec_df_rating.to_json(orient='records'))
    for record in records:
        record['explanation'] = {
            "reason": reason,
            "collaborative_score": record.get('score'),
            "similar_users": similar_users or None,
        }

    return records


def compute_popularity_scores(product_type, client_id=DEMO_CLIENT_ID):
    """Bayesian-smoothed popularity score computed from every registered interaction
    type, weighted by tier (same shrinkage idea as the classic IMDB weighted rating, but
    no fabricated 1-5 rating): v = number of distinct interactors, R = total weighted
    interaction volume, C = catalog average. Generalized from a purchase-only
    computation - a client using only the default purchase/view types will see a small
    view contribution here that didn't exist before (views are still weighted far lower,
    0.2 vs purchase's 3.0), which is the intended behavior for a vertical (e.g. a
    content site) that has no "purchase"-equivalent event at all."""
    interactions = fetch_all_interactions(product_type, client_id=client_id)
    if interactions.empty:
        return pd.DataFrame(columns=['work_id', 'interaction_count', 'popularity_score'])

    weights = get_client_event_type_weights(client_id)
    default_weight = EVENT_TIER_WEIGHTS[DEFAULT_EVENT_TIER]
    interactions = interactions.copy()
    interactions['weighted_quantity'] = interactions['quantity'] * interactions['event_type'].map(
        lambda et: weights.get(et, default_weight)
    )

    grouped = interactions.groupby('work_id')['weighted_quantity'].agg(
        interactor_count='count', interaction_count='sum'
    ).reset_index()

    m = grouped['interactor_count'].quantile(0.80)
    C = grouped['interaction_count'].mean()

    grouped['popularity_score'] = (
        grouped['interactor_count'] / (grouped['interactor_count'] + m) * grouped['interaction_count']
        + m / (m + grouped['interactor_count']) * C
    )

    return grouped[['work_id', 'interaction_count', 'popularity_score']]


def find_similar_users(product_type, user_id, top_n=2, client_id=DEMO_CLIENT_ID):
    """Users whose interaction history overlaps with this user's, across every
    registered event type (not just purchases) - used to explain a collaborative
    recommendation in plain terms."""
    interactions = fetch_all_interactions(product_type, client_id=client_id)
    if interactions.empty:
        return []

    users = get_data_users(product_type, user_id=None, count=None, client_id=client_id)
    name_map = dict(zip(users['user_id'], users['user_firstlastname']))

    user_id = int(user_id)
    user_history = interactions[interactions['user_id'] == user_id]
    selected_set = set(user_history['work_id'].dropna().astype(int))
    if not selected_set:
        return []

    overlaps = []
    for uid, grp in interactions.dropna(subset=['user_id']).groupby('user_id'):
        if int(uid) == user_id:
            continue
        shared = sorted(selected_set.intersection(set(grp['work_id'].dropna().astype(int))))
        if shared:
            overlaps.append((int(uid), shared))

    overlaps.sort(key=lambda x: len(x[1]), reverse=True)

    return [
        {"user_id": uid, "name": name_map.get(uid, f"User {uid}"), "shared_work_ids": shared}
        for uid, shared in overlaps[:top_n]
    ]


def add_ratings_from_purchases(data, data_purchase):

    # Application du Rating sur les oeuvres (fictif)
    data_ratings = pd.DataFrame(data_purchase)
    data_ratings_sorted = data_ratings.sort_values(by='work_id')

    data_ratings_2 = pd.DataFrame(data_purchase)
    data_ratings_2_sorted = data_ratings_2.sort_values(by='work_id')

    #Apply of a rating regarding purchases of the products
    data_ratings_2_sorted['rating'] = data_ratings_sorted['total_purchases'].apply(rating_to_movie)

    #print(df_info(data_ratings_sorted, 'data_ratings_sorted'))
    #print(df_info(data_ratings_2_sorted, 'data_ratings_2_sorted'))

    data_items = pd.DataFrame()
    data_items = data_ratings_sorted.groupby('work_id').size().reset_index(name='rating_count')
    data_items['rating_average'] = data_ratings_2_sorted.groupby('work_id')['rating'].transform('mean')

    #print(df_info(data_items, 'data_items'))
    # print(data_items.info())
    # print(data.info())
    # print(data_items['work_id'].dtype)

    data_items_merge = data_items.merge(data, on='work_id', how='left')

    data_items_merge['rating_average'] = data_items_merge['rating_count'].apply(rating_to_allpurchases_of_movies)

    #print(df_info(data_items_merge, 'data_items_merge'))

    # Calculate the number of votes garnered by the 80th percentile show
    m = data_items_merge['rating_count'].quantile(0.80)
    C = data_items_merge['rating_average'].mean()

    # Compute the score using the weighted_rating function defined above
    data_items_merge['score'] = data_items_merge.apply(lambda data_items_merge: weighted_rating(data_items_merge, m, C),
                                                       axis=1)

    data_items_merge = data_items_merge.sort_values('work_id', ascending=True)
    data_items_merge = data_items_merge.reset_index()

    return data_items_merge

def model_vectorization_cosine_similarities(data_works, data_similarities, stopwords_terms, typeOfVec = "CountVec"):

    if(typeOfVec == "Tfidf"):

        # Define a TF-IDF Vectorizer Object. Remove all french stopwords
        tfidf = TfidfVectorizer(stop_words=stopwords_terms)

        # Construct the required TF-IDF matrix by applying the fit_transform method on the overview feature
        matrix = tfidf.fit_transform(data_similarities)

        # Output the shape of tfidf_matrix
        # matrix.shape

    else:

        # Define a CV Vectorizer Object. Remove all french stopwords
        cv = CountVectorizer(stop_words=stopwords_terms)

        # Construct the required CV matrix by applying the fit_transform method on the overview feature
        matrix = cv.fit_transform(data_similarities)

        # Output the shape of tfidf_matrix
        # matrix.shape

    # Compute the cosine similarity matrix
    cosine_sim = cosine_similarity(matrix, matrix)
    indices = pd.Series(data_works.index, index=data_works['title'])

    return cosine_sim, indices


# In-memory cache for the (expensive) vectorization + full pairwise cosine similarity
# computation, which was previously recomputed from scratch on every single recommendation
# request - O(n^2) in catalog size. Keyed by (client_id, product_type); invalidated
# automatically whenever the underlying bag-of-words content changes (product added,
# edited or removed), by comparing against a hash of that content rather than relying on
# a manually-maintained version counter that could drift out of sync with actual writes.
_COSINE_SIM_CACHE: dict[tuple, dict] = {}


def get_cosine_similarities_cached(data_works, data_similarities, stopwords_terms, typeOfVec, client_id, product_type):
    cache_key = (client_id, product_type)
    signature = hash(tuple(data_similarities.tolist()))

    cached = _COSINE_SIM_CACHE.get(cache_key)
    if cached is not None and cached["signature"] == signature:
        return cached["cosine_sim"], cached["indices"]

    cosine_sim, indices = model_vectorization_cosine_similarities(
        data_works, data_similarities, stopwords_terms, typeOfVec=typeOfVec
    )
    _COSINE_SIM_CACHE[cache_key] = {
        "signature": signature,
        "cosine_sim": cosine_sim,
        "indices": indices,
    }
    return cosine_sim, indices



# Function that takes in shows title as input and gives recommendations
def model_content_recommender(title, cosine_sim, df, indices, limit=4,
                            with_score=False):
    # Obtain the index of the show that matches the title
    idx = indices[title]

    # Get the pairwise similarity scores of all shows with that show
    # And convert it into a list of tuples as described above
    sim_scores = list(enumerate(cosine_sim[idx]))

    # Sort the movies based on the cosine similarity scores
    sim_scores = sorted(sim_scores, key=lambda x: x[1], reverse=True)

    # Get the scores of the 10 most similar shows. Ignore the first movie.
    sim_scores = sim_scores[1:limit]

    # Get the show indices
    show_indices = [i[0] for i in sim_scores]

    # Return the top most similar show, with its actual cosine similarity value attached
    # (previously computed but discarded, so callers had no way to explain the ranking)
    rec = df.iloc[show_indices].copy()
    rec['content_similarity'] = [s for _, s in sim_scores]

    # Sort by score based on purchase - popularity
    if (with_score):
        rec = rec.sort_values('score', ascending=False)

    # Return the top 10 most similar movies
    return rec


def model_vector_db_init():

    #load_dotenv()
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "api", ".env"))
    pinecone_api_key = os.getenv("PINECONE_API_KEY")

    pc = Pinecone(api_key=pinecone_api_key)

    return pc

def pinecone_index_name(product_type, client_id=DEMO_CLIENT_ID):
    """Pinecone index names must be lowercase alphanumeric/hyphens, <=45 chars - our
    product_type allows uppercase/underscores, so it can't be used as-is. Each client
    also gets its own dedicated index (not just a shared-index namespace): a client's
    catalog can be fully deleted/rebuilt by dropping one index, with no risk of ever
    touching another client's vectors even if both pick the same product_type name."""
    slug = re.sub(r'[^a-z0-9-]+', '-', product_type.lower()).strip('-')
    name = f"client-{client_id}-{slug}"
    return name[:45].rstrip('-')


def model_vector_create_index(product_type, client_id=DEMO_CLIENT_ID):

    pc = model_vector_db_init()

    index_name = pinecone_index_name(product_type, client_id)

    if not pc.has_index(index_name):
        pc.create_index(
            name=index_name,
            dimension=384,
            metric="dotproduct",
            spec=ServerlessSpec(
                cloud='aws',
                region='us-east-1'
            )
        )

        # Wait for the index to be ready
    while not pc.describe_index(index_name).status['ready']:
        time.sleep(1)

    # Select Index
    index = pc.Index(index_name)
    return index

def model_vector_getModel():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = SentenceTransformer('sentence-transformers/all-MiniLM-L12-v2').to(device)
    return model

def model_generate_embeddings(data_similarities):

    model = model_vector_getModel()

    sentences = [x["bag_of_words"] for x in data_similarities]
    embeddings = model.encode(sentences)

    print("embeddings shape: ", embeddings.shape)

    return model, embeddings


def model_vector_indexing(data_works, data_similarities_prepared_for_vectors, product_type, client_id=DEMO_CLIENT_ID):

    # Select Index - dedicated to this client, so no cross-tenant mixing is possible
    # even before the namespace below is considered.
    index = model_vector_create_index(product_type, client_id=client_id)

    # Create embeddings
    model, embeddings = model_generate_embeddings(data_similarities_prepared_for_vectors)

    vectors = []
    for ds, dv, e in zip(data_works, data_similarities_prepared_for_vectors, embeddings):
        vectors.append({
            "id": dv['id'],
            "values": e,
            "metadata": {
                "title": ds["title"],
                "description": ds["description"],
                "genre": ds["genre_1"],
                "author": ds["author"],
                "year": ds["year"]
            }
        })

    index.upsert(
        vectors=vectors,
        namespace=product_type
    )

    time.sleep(10)  # Wait for the upserted vectors to be indexed

    index_info = index.describe_index_stats()
    print(index_info)
    total_vectors = index_info.get('total_vector_count')
    print(total_vectors)

    return index, model, total_vectors


def model_content_recommender_vectors(data_works, data_similarities, title, count, product_type, client_id=DEMO_CLIENT_ID):

    # Select Index - dedicated to this client
    index = model_vector_create_index(product_type, client_id=client_id)

    # Get Model
    model = model_vector_getModel()

    #index, model = model_vector_indexing(data_works, data_similarities)

    query = title

    query_embedding = model.encode(query).tolist()
    #print(query_embedding)

    results = index.query(
        namespace=product_type,
        vector=query_embedding,
        top_k=count,
        include_values=False,
        include_metadata=True,
        filter={"title": {"$ne": title}}
    )

    # Filter by threshold
    threshold = 0.7
    relevant_results = [res for res in results["matches"] if res["score"] >= threshold]

    # Print relevant results
    for res in relevant_results:
        print(f"ID: {res['id']}, Score: {res['score']}, Metadata: {res['metadata']}")

    filtered_results = [
        {"work_id": match["id"], "title": match["metadata"]["title"]}
        for match in results["matches"]
    ]

    return filtered_results
