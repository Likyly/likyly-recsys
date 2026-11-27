import sys
import os
import subprocess
import json
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
relative_path_utils = "../utils"
absolute_path_utils = os.path.abspath(os.path.join(current_dir, relative_path_utils))
sys.path.insert(0, absolute_path_utils)


from exploreData import *
from modelData import *

import solara
import plotly.express as px

import numpy as np
import pandas as pd


# Declare reactive variables at the top level. Components using these variables
# will be re-executed when their values change.
products_types = ["shoes", "movies", "books"]
products_type = solara.reactive("movies")

word_limit = solara.reactive(10)


@solara.component
def Page():
    solara.Markdown(
        f"""
        # Machine Learning - Recommandation de produits
        ## Création et évaluation du modèle de données
    """
    )

    ############################################################################
    ## Exploration et chargement des données
    ############################################################################

    solara.ToggleButtonsSingle(
        value=products_type,
        values=products_types,
        on_value=lambda val: products_type.set(val)
    )
    # solara.Markdown(f"**Selected**: {products_type.value}")

    products_type_selected = products_type.value

    result = train_model_for_dataset(products_type_selected, render=True)


def train_model_for_dataset(product_type_selected, render=False):
    print(f"\n=== Processing dataset: {product_type_selected} ===")

    model_dir = os.path.join(os.getcwd(), 'model')
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, f"{product_type_selected}_users_rating_model.npz")
    metrics_path = os.path.join(model_dir, f"{product_type_selected}_users_rating_metrics.json")

    if not os.path.exists(model_path):
        interactions = build_user_item_matrix(product_type_selected)
        _, precision = evaluate_model(interactions, k=10)
        users_rating_model = create_model(interactions)
        users_rating_model.save(model_path)
        print(f"[{product_type_selected}] Model saved to {model_path}, precision@10={precision:.4f}")

        push_target = f"model/{product_type_selected}_users_rating_model.npz"
        try:
            subprocess.run(["dvc", "add", push_target], check=True)
        except subprocess.CalledProcessError:
            pass
        try:
            subprocess.run(["dvc", "push", push_target], check=True)
            print(f"[{product_type_selected}] Model pushed to DVC remote.")
        except subprocess.CalledProcessError as e:
            print(f"[{product_type_selected}] DVC push failed: {e}")

        history = []
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path, "r", encoding="utf-8") as f:
                    history = json.load(f).get("history", [])
            except Exception:
                history = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        history.append({"timestamp": timestamp, "precision_at_10": precision})
        metrics_payload = {
            "dataset": product_type_selected,
            "precision_at_10": precision,
            "num_users": interactions.shape[0],
            "num_items": interactions.shape[1],
            "num_interactions": int(interactions.nnz),
            "timestamp": timestamp,
            "history": history,
        }
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics_payload, f, indent=2)
        subprocess.run(["dvc", "add", metrics_path], check=False)
        subprocess.run(["dvc", "push", metrics_path], check=False)

        previous_entry = history[-2] if len(history) > 1 else None

        if render:
            render_model_heading(product_type_selected, model_path)
            render_evaluation(metrics_payload, previous_entry)
    else:
        push_target = f"model/{product_type_selected}_users_rating_model.npz"
        try:
            subprocess.run(["dvc", "add", push_target], check=True)
        except subprocess.CalledProcessError:
            pass
        try:
            subprocess.run(["dvc", "push", push_target], check=True)
            print(f"[{product_type_selected}] Existing model synced to DVC remote.")
        except subprocess.CalledProcessError as e:
            print(f"[{product_type_selected}] Unable to push existing model to DVC: {e}")
        print(f"[{product_type_selected}] Model already exists locally. Skipping retrain.")
        if render:
            render_model_heading(product_type_selected, model_path)
            if os.path.exists(metrics_path):
                with open(metrics_path, "r", encoding="utf-8") as f:
                    metrics_payload = json.load(f)
                history = metrics_payload.get("history", [])
                previous_entry = history[-2] if len(history) > 1 else None
                render_evaluation(metrics_payload, previous_entry)
            else:
                solara.Markdown("Aucune métrique disponible pour ce modèle.")
        return None


def train_models_for_all():
    for dataset in products_types:
        train_model_for_dataset(dataset)


if __name__ == "__main__":
    train_models_for_all()
else:
    # The following line is required only when running the code in a Jupyter notebook:
    Page()


def render_evaluation(metrics_payload, previous_entry=None):
    product_type_selected = metrics_payload["dataset"]
    precision = metrics_payload["precision_at_10"]

    solara.Markdown(f"### Évaluation du modèle - {product_type_selected.title()}")
    solara.Markdown(
        f"**Precision@10** : {precision:.4f} "
        f"({metrics_payload['num_users']} utilisateurs, {metrics_payload['num_items']} produits, "
        f"{metrics_payload['num_interactions']} interactions)"
    )
    if previous_entry:
        solara.Markdown(
            f"**Modèle précédent du {previous_entry['timestamp']}** — "
            f"Precision@10 : {previous_entry['precision_at_10']:.4f}"
        )
    solara.Markdown("### Technique de création du modèle")
    solara.Markdown(
        "Factorisation matricielle par feedback implicite (ALS, bibliothèque `implicit`) entraînée "
        "directement sur les compteurs d'achats et de vues pondérés par confiance, sans note fictive. "
        "La precision@10 est mesurée sur un jeu de test mis de côté (20% des interactions)."
    )

    history = metrics_payload.get("history", [])
    if len(history) > 1:
        df_history = pd.DataFrame(history)
        fig = px.line(df_history, x='timestamp', y='precision_at_10', markers=True)
        solara.FigurePlotly(fig)


def render_model_heading(product_type_selected, model_path):
    try:
        modified_ts = datetime.fromtimestamp(os.path.getmtime(model_path))
        modified_str = modified_ts.strftime('%Y-%m-%d %H:%M')
    except FileNotFoundError:
        modified_str = "date inconnue"
    solara.Markdown(f"### Modèle {product_type_selected.title()}")
    solara.Markdown(f"##### Date de création : {modified_str}")
    solara.Markdown(f"##### Nom du modèle : {os.path.basename(model_path)}")
