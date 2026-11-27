import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
relative_path_utils = "../utils"
absolute_path_utils = os.path.abspath(os.path.join(current_dir, relative_path_utils))
sys.path.insert(0, absolute_path_utils)


from exploreData import *
from modelData import *

import solara

import numpy as np
import pandas as pd


# Declare reactive variables at the top level. Components using these variables
# will be re-executed when their values change.
products_types = ["shoes", "movies", "books"]
products_type = solara.reactive("movies")
show_raw_tables = solara.reactive(False)

word_limit = solara.reactive(10)

# default value for select of rec select
select_default_user = solara.reactive("Arnaud Breton")

#default value for iteration of data evaluation model
slider_rmse_iteration_default = solara.reactive(0)

@solara.component
def Page():

    solara.Style(
        """
        .v-application--wrap {
            padding:1em; !important
        }
        .product-select {
            max-width: 320px;
        }
        """
    )

    solara.Markdown(
        f"""
        # Machine Learning - Recommandation de produits
    """
    )

    solara.Markdown(
        f"""
        ## Suggestions personnalisées en fonction du profil utilisateur
    """
    )

    solara.Markdown(
        """Notre modèle utilise du filtrage collaboratif pur : il analyse les achats et notes pour identifier les utilisateurs aux goûts similaires. En factorisant la matrice “utilisateurs × produits”, il apprend des profils latents qui révèlent quelles personnes se ressemblent. Le modèle peut alors recommander à un utilisateur les produits appréciés par d’autres clients au comportement proche, même sans connaître la nature des articles."""
    )
    solara.Markdown(
        "Par exemple, Arnaud Breton a visionné plusieurs thrillers futuristes ; nous lui suggérons donc *Tenet* ou *The Killer*, très appréciés par Emily Davis et Michael Johnson qui partagent un historique similaire."
    )

    solara.ToggleButtonsSingle(value=products_type, values=products_types)
    solara.Switch(label="Afficher les tableaux bruts", value=show_raw_tables)

    products_type_selected = products_type.value

    data = get_data(product_type=products_type_selected, product_id=None, count=None)
    data_users = get_data_users(product_type=products_type_selected,user_id=None, count=None)
    data_purchase = get_data_users_purchases(product_type=products_type_selected, user_id=None, count=None)
    for col in ('user_id', 'work_id'):
        if col in data_purchase.columns:
            data_purchase[col] = pd.to_numeric(data_purchase[col], errors='coerce').astype('Int64')
    columns_needed = ['work_id', 'title', 'genre_1']
    if 'price' in data.columns:
        columns_needed.append('price')
    purchase_details = data_purchase.merge(
        data[columns_needed],
        on='work_id',
        how='left'
    )
    user_name_map = dict(zip(data_users['user_id'], data_users['user_firstlastname']))


    ############################################################################
    ## Check for a trained model
    ############################################################################
    try:
        users_rating_model = load_model(products_type_selected)
        model_exists = True
    except FileNotFoundError:
        model_exists = False

    if model_exists:

        ############################################################################
        ## Rec Sys
        ############################################################################

        select_data_users = np.array(data_users['user_firstlastname']).tolist()
        select_data_users_id = np.array(data_users['user_id']).tolist()

        solara.Select(label="Utilisateurs", value=select_default_user, values=select_data_users, dense=True, classes=["product-select"])

        if select_default_user.value != 'Choisir...':
            sv = select_default_user.value
        else:
            sv = 'Arnaud Breton'

        rec_df_search_select = data_users[data_users['user_firstlastname'] == sv]
        #display_data(rec_df_search_select)
        selected_user_id = int(rec_df_search_select['user_id'].iloc[0])

        user_history = purchase_details[purchase_details['user_id'] == selected_user_id]
        solara.Markdown(f"### Profil utilisateur – {sv}")
        if not user_history.empty:
            top_genres = user_history['genre_1'].value_counts().head(2)
            if not top_genres.empty:
                genres_text = ", ".join([f"{genre} ({count})" for genre, count in top_genres.items()])
                solara.Markdown(f"**Genres dominants** : {genres_text}")
            top_items = user_history.sort_values('total_purchases', ascending=False).head(3)
            lines = [
                f"- {row['title']} – {row['genre_1']} (achats: {row['total_purchases']})"
                for _, row in top_items.iterrows()
            ]
            solara.Markdown("**Historique récent** :\n" + "\n".join(lines))
        else:
            solara.Markdown("_Pas encore d'achats enregistrés pour cet utilisateur._")

        selected_set = set(user_history['work_id'].dropna().astype(int))
        similar_users = []
        similar_user_ids = set()
        if selected_set:
            grouped = purchase_details.dropna(subset=['user_id']).groupby('user_id')
            for uid, grp in grouped:
                if uid == selected_user_id:
                    continue
                overlap = selected_set.intersection(set(grp['work_id']))
                if overlap:
                    similar_users.append((uid, len(overlap), overlap))
            similar_users.sort(key=lambda x: x[1], reverse=True)
            if similar_users:
                similar_user_ids = {uid for uid, _, _ in similar_users}
                solara.Markdown("**Utilisateurs aux goûts proches** :")
                summary = []
                for uid, count_overlap, overlap in similar_users[:2]:
                    name = user_name_map.get(uid, f"Utilisateur {uid}")
                    sample_titles = data[data['work_id'].isin(list(overlap))]['title'].head(2).tolist()
                    titles_txt = ", ".join(sample_titles)
                    summary.append(f"- {name} (goûts communs: {count_overlap}) – ex: {titles_txt}")
                solara.Markdown("\n".join(summary))

        # Use of the Torch model loaded
        rec_df_rating_from_select = predict_items_from_user(users_rating_model, data, int(rec_df_search_select['user_id']), 10)
        if show_raw_tables.value:
            display_data(rec_df_rating_from_select)

        owned_ids = set(user_history['work_id'].dropna().astype(int))
        if owned_ids:
            rec_df_rating_from_select = rec_df_rating_from_select[
                ~rec_df_rating_from_select['work_id'].isin(owned_ids)
            ]
        rec_df_rating_from_select = rec_df_rating_from_select.head(3)

        #score_model(users_rating_model, onp_users_rating_test)

        #solara.Markdown(
        #    f"""
        #    ## Best RMSE iteration number verified:{best_rmse}
        #"""
        #)

        solara.Markdown(f"### Suggestions de produits que {sv} appréciera, plébiscités par d'autres utilisateurs")
        with solara.Row(gap="10px", justify="space-around"):

            for index, row in rec_df_rating_from_select.head(3).iterrows():

                show_title = row['title']
                show_genre = row['genre_1']
                show_year = str(row['year'])
                show_score = f"{row['score']:.5f}" if pd.notna(row['score']) else "-"
                show_info = f"{show_year} - {show_genre} "
                show_price_raw = row.get('price')
                price_text = None
                if products_type_selected == 'shoes' and show_price_raw is not None:
                    if pd.notna(show_price_raw):
                        try:
                            price_value = float(show_price_raw)
                            price_text = f"{price_value:,.2f} €"
                        except (TypeError, ValueError):
                            price_text = f"{show_price_raw} €"

                with solara.Card(
                    title=show_title,
                    subtitle=show_info,
                    style="text-align: center; width: 320px; margin: auto;",
                    classes=["d-flex", "flex-column", "align-center"],
                    elevation=4,
                ):

                    if price_text:
                        solara.Markdown(f"Prix : {price_text}", style="text-align: center; font-weight: 600;")

                    if pd.notna(row['url']):
                        image_url = row['url']
                    else:
                        image_url = 'https://placehold.co/300x400?text=No%20Image'

                    solara.Column([
                        solara.Image(
                           image=image_url,
                            width="220px"
                        )
                    ], align="center")

                    solara.Markdown(f"Score : {show_score}", style="text-align: center; font-weight: bold;")

                    other_buyers = purchase_details[
                        (purchase_details['work_id'] == row['work_id']) &
                        (purchase_details['user_id'] != selected_user_id)
                    ]
                    if similar_user_ids:
                        other_buyers = other_buyers[other_buyers['user_id'].isin(similar_user_ids)]
                    if not other_buyers.empty:
                        other_names = [
                            user_name_map.get(uid, f"Utilisateur {uid}")
                            for uid in other_buyers['user_id'].unique()[:2]
                        ]
                        solara.Markdown(
                            f"_Également apprécié par : {', '.join(other_names)}_",
                            style="text-align: center;"
                        )
    else:

        solara.Markdown(
            f"""
            # Please, create the Machine Learning Model first
        """
        )

Page()
