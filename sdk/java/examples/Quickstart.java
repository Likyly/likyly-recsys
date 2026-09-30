// Quick start: catalog -> events -> recommendations -> attribution.
//
// Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... java -cp <sdk jar and Jackson> examples/Quickstart.java
// region:imports
import com.likyly.Likyly;
import com.likyly.error.NotFoundException;
import com.likyly.error.RateLimitException;
import com.likyly.model.EventInput;
import com.likyly.model.HybridOptions;
import com.likyly.model.ItemImport;
import com.likyly.model.RecommendationRequest;
import com.likyly.model.RecommendationResponse;
import com.likyly.model.SessionOptions;
import com.likyly.model.SimilarOptions;
import com.likyly.model.TypedEventInput;
import com.likyly.model.UserInput;
import java.time.Duration;
import java.util.List;
import java.util.Map;
// endregion

public class Quickstart {
    public static void main(String[] args) {
        // region:initialize
        // Backend only: the secret key gives access to your catalog and your users.
        Likyly likyly = Likyly.builder()
            .apiKey(System.getenv("LIKYLY_SECRET_KEY"))
            .baseUrl(System.getenv().getOrDefault("LIKYLY_BASE_URL", Likyly.DEFAULT_BASE_URL)) // docs:omit
            .catalog(System.getenv("LIKYLY_CATALOG")) // docs:omit
            .build();
        // endregion

        // region:catalog
        // Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
        likyly.items().upsertMany(List.of(
            new ItemImport("SKU-1", "Nike Air Max", "Running shoe with air cushioning", Map.of("category", "shoes", "brand", "Nike", "price", 129.9)),
            new ItemImport("SKU-2", "Adidas Ultraboost", "Responsive running shoe", Map.of("category", "shoes", "brand", "Adidas", "price", 149)),
            new ItemImport("SKU-3", "Nike Pegasus", "Everyday running shoe", Map.of("category", "shoes", "brand", "Nike", "price", 119))));

        // Users are optional: describe them if you want the profile to travel with their events.
        likyly.users().upsert("user_123", new UserInput(Map.of("country", "FR", "segment", "premium")));
        // endregion

        // region:track
        // Tell LIKYLY what your visitors do.
        likyly.events().view(EventInput.builder().userId("user_123").itemId("SKU-1").build());

        // Purchases carry an eventId: replaying the call can never count the sale twice.
        likyly.events().purchase(EventInput.builder()
            .eventId("purchase_order_9281_SKU-1")
            .userId("user_123")
            .itemId("SKU-1")
            .quantity(1)
            .properties(Map.of("price", 129.9, "currency", "EUR", "orderId", "order_9281"))
            .build());
        // endregion

        // region:recommend
        // Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
        RecommendationResponse recs = likyly.recommendations().get(
            RecommendationRequest.builder().userId("user_123").placement("homepage").limit(3).build());

        System.out.println("strategy: " + recs.strategy());
        recs.items().forEach(item -> System.out.println(item.itemId() + " " + item.title()));
        // endregion

        // region:showcase
        // Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
        RecommendationResponse productRecs = likyly.recommendations().get(RecommendationRequest.builder()
            .userId("user_123") // visiteur connecté
            .sessionId("sess_abc") // ou visiteur anonyme (cookie)
            .itemId("SKU-1") // fiche produit en cours de consultation
            .placement("product_page") // où elles seront affichées (libre)
            .limit(6) // combien d'articles (10 par défaut)
            .build());

        productRecs.items().forEach(item -> System.out.println(item.itemId() + " " + item.title() + " " + item.score()));
        // endregion

        // region:attribution
        // Send the recommendationId back: LIKYLY measures which recommendations get seen, clicked and bought.
        EventInput shown = EventInput.builder()
            .userId("user_123")
            .itemId(recs.items().get(0).itemId())
            .recommendationId(recs.recommendationId())
            .placement("homepage")
            .build();
        likyly.events().impression(shown);
        likyly.events().click(shown);
        // endregion

        // region:browser
        // Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
        Likyly front = Likyly.builder()
            .apiKey(System.getenv("LIKYLY_PUBLIC_KEY"))
            .baseUrl(System.getenv().getOrDefault("LIKYLY_BASE_URL", Likyly.DEFAULT_BASE_URL)) // docs:omit
            .catalog(System.getenv("LIKYLY_CATALOG")) // docs:omit
            .build();

        // A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
        front.events().view(EventInput.builder().sessionId("sess_abc").itemId("SKU-2").build());
        RecommendationResponse forVisitor = front.recommendations().get(
            RecommendationRequest.builder().sessionId("sess_abc").viewedItemIds(List.of("SKU-2")).placement("product_page").limit(3).build());
        // endregion

        // region:custom
        // Any string is a valid event type: track what matters to your business.
        likyly.events().track("favorite", EventInput.builder().userId("user_123").itemId("SKU-2").properties(Map.of("list", "wishlist")).build());

        // Up to 1000 events per call, each with its own type.
        likyly.events().trackMany(List.of(
            TypedEventInput.of("view", EventInput.builder().userId("user_123").itemId("SKU-3").build()),
            TypedEventInput.of("add_to_cart", EventInput.builder().userId("user_123").itemId("SKU-3").quantity(1).build())));
        // endregion

        // region:advanced
        // Advanced Recommendations: one strategy at a time, when you want to choose.
        var similar = likyly.recommendations().similar(SimilarOptions.builder().itemId("SKU-1").limit(3).build());
        var hybrid = likyly.recommendations().hybrid(HybridOptions.builder().userId("user_123").itemId("SKU-1").alpha(0.7).limit(3).build());
        var session = likyly.recommendations().session(SessionOptions.builder().viewedItemIds(List.of("SKU-1", "SKU-2")).limit(3).build());
        // endregion

        // region:config
        Likyly tuned = Likyly.builder()
            .apiKey(System.getenv("LIKYLY_SECRET_KEY"))
            .timeout(Duration.ofSeconds(5)) // per attempt (default 10 s)
            .maxRetries(3) // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
            .userAgent("my-shop/1.4") // appended to the SDK's User-Agent
            .baseUrl(System.getenv().getOrDefault("LIKYLY_BASE_URL", Likyly.DEFAULT_BASE_URL)) // docs:omit
            .build();
        // endregion

        // region:errors
        try {
            likyly.items().get("does-not-exist");
        } catch (NotFoundException e) {
            System.out.println("no such item, request " + e.requestId());
        } catch (RateLimitException e) {
            System.out.println("slow down, retry in " + e.retryAfter());
        }
        // endregion

        System.out.println("visitor strategy: " + forVisitor.strategy());

        // region:cleanup
        likyly.items().deleteMany(List.of("SKU-1", "SKU-2", "SKU-3"));
        likyly.users().delete("user_123");
        // endregion
        System.out.println("quickstart ok");
    }
}
