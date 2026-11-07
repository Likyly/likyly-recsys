// Example: LIKYLY in an Android app (ViewModel + Activity). Not compiled with the SDK - copy it into your app.
//
// build.gradle.kts (app):   implementation("com.likyly:likyly-android:1.0.0")
// local.properties / CI:    LIKYLY_PUBLIC_KEY=pk_...   (exposed as BuildConfig.LIKYLY_PUBLIC_KEY)

package com.example.shop

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.likyly.EventInput
import com.likyly.FileAnonymousIdStore
import com.likyly.Likyly
import com.likyly.LikylyException
import com.likyly.LikylyOptions
import com.likyly.LikylySession
import com.likyly.PausedException
import com.likyly.PlacementContext
import com.likyly.PlacementRequest
import com.likyly.RecommendedItem
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/** One client and one session for the whole app: build them in your Application class. */
class ShopApp : Application() {
    lateinit var likyly: Likyly
        private set
    lateinit var session: LikylySession
        private set

    override fun onCreate() {
        super.onCreate()
        likyly = Likyly(BuildConfig.LIKYLY_PUBLIC_KEY, LikylyOptions(userAgent = "shop-android/${BuildConfig.VERSION_NAME}"))
        session = LikylySession(FileAnonymousIdStore(filesDir))
    }
}

sealed interface RelatedState {
    object Loading : RelatedState
    data class Shown(val recommendationId: String, val items: List<RecommendedItem>) : RelatedState
    /** LIKYLY unavailable: show your own content (best sellers, same category...). */
    object Fallback : RelatedState
}

class ProductViewModel(app: Application) : AndroidViewModel(app) {
    private val likyly = (app as ShopApp).likyly
    private val session = (app as ShopApp).session
    private var userId: String? = null // set when the visitor is signed in

    private val _related = MutableStateFlow<RelatedState>(RelatedState.Loading)
    val related: StateFlow<RelatedState> = _related

    private val impressed = mutableSetOf<String>()
    private var placement = "pdp-related"

    /** The product screen opened. */
    fun onProductShown(productId: String) {
        viewModelScope.launch {
            // What the visitor looked at: LIKYLY learns from it.
            runCatching { likyly.events.view(EventInput(itemId = productId, userId = userId, sessionId = session.anonymousId)) }

            _related.value = try {
                val result = likyly.recommend(
                    PlacementRequest(
                        placement = placement,
                        context = PlacementContext(itemId = productId, userId = userId, sessionId = session.anonymousId),
                        limit = 4,
                    ),
                )
                if (result.items.isEmpty()) RelatedState.Fallback else RelatedState.Shown(result.recommendationId, result.items)
            } catch (e: PausedException) {
                RelatedState.Fallback // LIKYLY failed moments ago: no waiting, show your own content
            } catch (e: LikylyException) {
                RelatedState.Fallback
            }
        }
    }

    /** A recommended card became visible on screen (e.g. from RecyclerView / LazyRow). Once per card. */
    fun onCardVisible(item: RecommendedItem) {
        val state = _related.value as? RelatedState.Shown ?: return
        if (!impressed.add(item.itemId)) return
        viewModelScope.launch {
            runCatching {
                likyly.events.impression(
                    EventInput(
                        itemId = item.itemId, userId = userId, sessionId = session.anonymousId,
                        recommendationId = state.recommendationId, placement = placement,
                    ),
                )
            }
        }
    }

    /** The visitor tapped a recommended card. */
    fun onCardTapped(item: RecommendedItem) {
        val state = _related.value as? RelatedState.Shown ?: return
        viewModelScope.launch {
            runCatching {
                likyly.events.click(
                    EventInput(
                        itemId = item.itemId, userId = userId, sessionId = session.anonymousId,
                        recommendationId = state.recommendationId, placement = placement,
                    ),
                )
            }
        }
    }

    /** Order confirmed: one purchase event per line, with an eventId so a retry never counts it twice. */
    fun onOrderConfirmed(orderId: String, lines: List<Pair<String, Int>>) {
        viewModelScope.launch {
            for ((itemId, quantity) in lines) {
                runCatching {
                    likyly.events.purchase(
                        EventInput(
                            itemId = itemId, userId = userId, sessionId = session.anonymousId,
                            eventId = "purchase_${orderId}_$itemId", quantity = quantity,
                        ),
                    )
                }
            }
        }
    }

    /** The visitor signed in: their anonymous history now counts for their account. */
    fun onSignedIn(newUserId: String) {
        userId = newUserId
        viewModelScope.launch { runCatching { session.identify(likyly, newUserId) } }
    }
}
