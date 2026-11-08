# App Shopify LIKYLY — cadrage

Décidé le 2026-11-08 : après WordPress, Shopify, puis Magento (Open Source). App **gratuite** au
lancement (pas de facturation Shopify).

## Objectif

Le marchand installe l'app depuis l'App Store, relie son compte LIKYLY, et obtient en quelques
minutes, sans toucher au code de son thème :

- « Vous aimerez aussi » sur la fiche produit (similarité de contenu) ;
- « Recommandé pour vous » (filtrage collaboratif, visiteur connecté ou session) ;
- « Tendances » (accueil, collections) ;
- « Complétez votre panier » (page panier) ;
- un onglet Performance : impressions, clics, ventes attribuées.

C'est le même produit que le plugin WordPress, adapté à Shopify.

## Ce que Shopify impose (vérifié le 2026-11-08)

| Règle | Conséquence pour nous |
|---|---|
| Nouvelles apps publiques : **GraphQL Admin API uniquement** (REST est « legacy » depuis oct. 2024, obligatoire depuis avril 2025) | L'app lit le catalogue en GraphQL. Le connecteur `shopify` du backend (REST `products.json`) ne peut pas servir à l'app publique. |
| **Jetons hors ligne expirants** obligatoires pour les nouvelles apps publiques (depuis avril 2026) : jeton valable 1 h, jeton de rafraîchissement valable 90 jours | Le jeton Shopify doit vivre là où on sait le rafraîchir : dans l'app, pas dans le backend LIKYLY. |
| Storefront : **theme app extensions** obligatoires, aucune modification du code du thème | Blocs d'app + intégration d'app (*app embed*), activés depuis l'éditeur de thème. |
| App intégrée à l'admin : **dernière App Bridge**, sans cookies tiers ni localStorage | Template officiel Shopify (React Router), authentification par jeton de session. |
| Facturation : toute facturation hors Shopify interdit l'App Store | Gratuit maintenant. Le jour où le Pro devient payant, pour les marchands Shopify il passera par le Managed Pricing ou la Billing API de Shopify. La demande de Pro actuelle (sans paiement) reste possible. |
| Webhooks de conformité RGPD : `customers/data_request`, `customers/redact`, `shop/redact` | À traiter (voir plus bas). |
| Revue : vidéo de l'onboarding, instructions de test, **compte LIKYLY de test sans 2FA** qui reste valide après publication | À préparer pour la soumission. |

Sources : [GraphQL obligatoire](https://shopify.dev/changelog/starting-april-2025-new-public-apps-submitted-to-shopify-app-store-must-use-graphql),
[jetons expirants](https://shopify.dev/changelog/expiring-offline-access-tokens-required-for-public-apps-april-1-2026),
[exigences de l'App Store](https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements).

## Architecture

```
Admin Shopify ──(App Bridge, jeton de session)──► app likyly-shopify (Node, shopify.likyly.com)
                                                   │  OAuth, jetons Shopify, webhooks
                                                   │  GraphQL Admin API (catalogue)
                                                   └──(clé secrète LIKYLY)──► API LIKYLY
                                                                              /items/import, /items/delete,
                                                                              /placements, /analytics/*, /users

Vitrine ──(app embed + blocs d'app, clé publique)──► API LIKYLY (recommandations, impressions, clics)
Checkout ──(Web Pixel, clé publique)──► API LIKYLY (vue produit, ajout au panier, achat)
```

**Le choix clé : l'app pousse le catalogue vers LIKYLY, comme le plugin WordPress.** C'est l'app qui
garde et rafraîchit le jeton Shopify, et qui envoie les produits par `/items/import` (déjà utilisé
par WordPress). Le backend n'a pas à gérer OAuth ni les jetons expirants de Shopify, et le
connecteur `shopify` existant reste ce qu'il est : une source pour les marchands qui créent leur
propre app personnalisée (les jetons `shpat_` de ces apps n'expirent pas, et REST y fonctionne
encore).

### 1. L'app (nouveau dépôt `Likyly/likyly-shopify`)

- Template officiel Shopify CLI (React Router, Node), configuration dans `shopify.app.toml`
  (installation gérée par Shopify, scopes `read_products`, plus `read_orders` si on veut les
  meilleures ventes, voir « Plan Free »).
- Hébergée sur le droplet (Docker derrière Caddy, `shopify.likyly.com`). Base pour les sessions
  Shopify et le lien boutique → clés LIKYLY (clé secrète chiffrée).
- **Écrans de l'admin** (Polaris), les mêmes onglets que WordPress :
  - *Connexion* : coller les deux clés LIKYLY (lien « Créer un compte gratuit » vers likyly.com).
    Ensuite, l'app crée le catalogue et les placements, et lance la première synchronisation.
  - *Blocs* : liens directs vers l'éditeur de thème pour activer l'app embed et ajouter chaque bloc
    (fiche produit, accueil, panier), avec l'état de chaque placement (`/placements/{slug}/health`).
  - *Synchronisation* : nombre de produits envoyés, rejets, bouton « Resynchroniser ».
  - *Performance* : `/analytics/summary`, `/placements`, `/items`.
- **Catalogue** : synchronisation complète par *bulk operation* GraphQL, puis webhooks
  `products/create`, `products/update`, `products/delete` pour les changements. Item id = id
  numérique du produit, `url` = handle, prix et stock de la première variante, image principale,
  type, tags et collections en propriétés (pour les filtres des placements).
- **Webhooks** :
  - `app/uninstalled` : on arrête la synchro, on désactive les placements et on supprime les
    jetons Shopify ;
  - `customers/redact` : `DELETE /users/{customer_id}` (existe déjà : profil et événements) ;
  - `customers/data_request` : on renvoie au marchand ce qu'on stocke pour ce client (ses
    événements) ;
  - `shop/redact` (48 h après la désinstallation) : on supprime le catalogue et les événements de la
    boutique.

### 2. Theme app extension (vitrine)

- **App embed « LIKYLY »** (un interrupteur à activer, une seule fois) : charge le SDK, gère la
  session anonyme, fait `identify` quand le client est connecté (`customer.id` en Liquid → `user_id`),
  envoie impressions et clics, et garde la trace du clic (`likyly_rec`) pour attribuer la vente.
- **Blocs d'app** que le marchand place dans l'éditeur : « Vous aimerez aussi », « Recommandé pour
  vous », « Tendances », « Complétez votre panier ». Réglages dans l'éditeur : titre, nombre de
  produits, colonnes.
- **Affichage** : Liquid ne peut pas appeler notre API côté serveur. Le bloc demande donc les ids
  à LIKYLY depuis le navigateur (clé publique), puis lit `/products/{handle}.js` sur la boutique.
  On obtient ainsi le prix dans la devise et la langue du visiteur (Shopify Markets) et la
  disponibilité réelle. Les cartes produit reprennent les styles du thème (polices, couleurs) ;
  on ne copie pas le HTML du thème.
- **Performance de la vitrine** : script léger, chargé en différé, pas de bibliothèque tierce.
  C'est un point surveillé par la revue Shopify et par le badge « Built for Shopify ».

### 3. Web Pixel (événements)

- Extension Web Pixel, abonnée à `product_viewed`, `product_added_to_cart` et
  `checkout_completed`.
- L'achat part avec le numéro de commande comme `event_id` (idempotent). Il contient les lignes
  de la commande et l'attribution lue dans le stockage du navigateur (l'API `browser` du pixel),
  là où l'app embed l'a écrite au clic.
- Consentement : les pixels respectent l'API Customer Privacy de Shopify. Sans consentement
  (fréquent en Europe), aucun événement ne part, mais les recommandations s'affichent quand même.
  Il faudra l'expliquer dans la fiche de l'app et dans l'onglet Performance.

### 4. Backend LIKYLY (peu de changements)

1. **Identifier l'intégration `shopify`** dans les appels, comme le plugin WordPress, pour que
   `/admin/integrations` la voie.
2. **Effacement d'une boutique** pour `shop/redact` : vérifier qu'on peut supprimer un catalogue
   entier (items et événements) avec la clé secrète. Sinon, ajouter un endpoint.
3. **Rien à faire pour les clés publiques sur un domaine quelconque**, ça marche déjà pour
   WordPress. Seul point à confirmer : le Web Pixel tourne dans un iframe isolé (origine `null`
   possible), donc le CORS de la clé publique doit l'accepter.
4. **Plus tard** : réécrire le connecteur `shopify` en GraphQL. Il n'est pas bloquant pour l'app,
   mais REST finira par disparaître aussi pour les apps personnalisées.

## Plan Free : le point produit à trancher

Le plan Free garde **50 produits**, et beaucoup de boutiques Shopify en ont davantage. WordPress
envoie les meilleures ventes d'abord. Sur Shopify, l'API Admin ne trie pas les produits par ventes :
il faut soit lire les commandes récentes (scope `read_orders`, plus délicat à justifier en revue),
soit prendre les produits les plus récents ou une collection choisie par le marchand. Je propose au
départ une **collection choisie par le marchand** (par défaut : toute la boutique, les 50 plus
récents), sans demander `read_orders`.

## Étapes

1. **Boutique de développement** : la créer, puis faire tourner pour de vrai
   `likyly-cms-sandboxes/shopify/import.mjs` (jamais testé contre une boutique) pour avoir
   120 produits et un blog.
2. **Squelette de l'app** : `Likyly/likyly-shopify` à partir du template, OAuth avec jetons
   expirants, écran *Connexion*, déploiement sur `shopify.likyly.com`.
3. **Catalogue** : bulk operation → `/items/import`, webhooks produits, écran *Synchronisation*.
4. **Vitrine** : app embed et les quatre blocs, essayés sur Dawn puis sur 2 ou 3 autres thèmes
   gratuits (Horizon, Sense, Craft…).
5. **Web Pixel** : vues, ajouts au panier et achats attribués ; vérification dans
   `/placements/{slug}/recent-events`.
6. **Conformité et Performance** : les webhooks RGPD, la désinstallation propre, l'onglet
   Performance.
7. **Soumission** : fiche App Store en français et en anglais (textes, captures, vidéo), politique
   de confidentialité, compte LIKYLY de test sans 2FA, instructions de test. Compter 1 à 3 semaines
   de revue.

À trancher avant l'étape 2 : le nom affiché sur l'App Store, et la règle du plan Free ci-dessus.
