package com.likyly

import java.io.File
import java.io.IOException
import java.util.UUID

/** Where [LikylySession] keeps the visitor's anonymous id. */
public interface AnonymousIdStore {
    public fun read(): String?
    public fun write(id: String)
}

/**
 * Keeps the anonymous id in a small file - on Android pass `context.filesDir` (private to the app, removed on
 * uninstall; exclude it in your backup rules if it should not follow the user to a new device).
 */
public class FileAnonymousIdStore(dir: File, fileName: String = "likyly_anonymous_id") : AnonymousIdStore {
    private val file = File(dir, fileName)

    override fun read(): String? = try {
        if (file.exists()) file.readText(Charsets.UTF_8).trim().ifEmpty { null } else null
    } catch (_: IOException) {
        null
    }

    override fun write(id: String) {
        try {
            file.parentFile?.mkdirs()
            file.writeText(id, Charsets.UTF_8)
        } catch (_: IOException) {
            // Unwritable storage: the id lives for this process only, which is still correct.
        }
    }
}

/** Keeps the anonymous id in memory only (tests, or apps that persist it themselves). */
public class InMemoryAnonymousIdStore(private var id: String? = null) : AnonymousIdStore {
    override fun read(): String? = id
    override fun write(id: String) {
        this.id = id
    }
}

/**
 * A first-party anonymous visitor id - generated once per install and persisted by [store] (never an advertising
 * id, never sent to anyone but LIKYLY). Send it as `sessionId` on events and `anonymousId`/`sessionId` in a
 * placement context until the visitor signs in, then call [identify] to attach their history to the user.
 */
public class LikylySession(private val store: AnonymousIdStore) {
    @Volatile private var cached: String? = null

    /** The visitor's anonymous id, created and persisted on first use. */
    public val anonymousId: String
        get() {
            cached?.let { return it }
            synchronized(this) {
                cached?.let { return it }
                val id = store.read() ?: ("anon_" + UUID.randomUUID().toString()).also { store.write(it) }
                cached = id
                return id
            }
        }

    /**
     * Call once you know the visitor's [userId] (right after login or sign-up): this session's past interactions
     * now count as that user's history. Safe to call more than once.
     */
    public suspend fun identify(likyly: Likyly, userId: String): IdentifyResult =
        likyly.events.identify(userId = userId, sessionId = anonymousId)
}
