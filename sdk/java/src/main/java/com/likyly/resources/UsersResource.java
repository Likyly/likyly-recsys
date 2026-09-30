package com.likyly.resources;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.error.ValidationException;
import com.likyly.internal.HttpCore;
import com.likyly.internal.Support;
import com.likyly.internal.Wire;
import com.likyly.model.BatchResult;
import com.likyly.model.ListOptions;
import com.likyly.model.User;
import com.likyly.model.UserImport;
import com.likyly.model.UserInput;
import com.likyly.model.UserList;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * {@code likyly.users()} - optional user profiles. Needs the <b>secret</b> API key (profiles are personal data).
 * You don't have to create a user before sending events for them.
 */
public final class UsersResource {
    private final HttpCore http;

    public UsersResource(HttpCore http) {
        this.http = http;
    }

    public User get(String userId) {
        return Wire.user(http.request("GET", "/users/" + HttpCore.encode(Support.requireId(userId, "userId")), Map.of(), null, true).data());
    }

    /** Every user (the API's behaviour without a {@code limit}). */
    public UserList list() {
        return list(new ListOptions(null, null));
    }

    public UserList list(ListOptions options) {
        Map<String, String> query = new LinkedHashMap<>();
        if (options.limit() != null) {
            query.put("limit", String.valueOf(options.limit()));
        }
        if (options.offset() != null) {
            query.put("offset", String.valueOf(options.offset()));
        }
        HttpCore.Result r = http.request("GET", "/users", query, null, true);
        List<User> users = new ArrayList<>();
        r.data().forEach(n -> users.add(Wire.user(n)));
        String total = r.headers().get("x-total-count");
        return new UserList(users, total == null ? null : Long.valueOf(total), options.limit(), options.offset() == null ? 0 : options.offset());
    }

    /** Creates or replaces the profile (idempotent). {@code properties} is free-form: country, segment, language, ... */
    public User upsert(String userId, UserInput user) {
        ObjectNode body = Support.object();
        Support.putProperties(body, user.properties());
        return Wire.user(http.request("PUT", "/users/" + HttpCore.encode(Support.requireId(userId, "userId")), Map.of(), body, true).data());
    }

    /** Erases the user: the profile <b>and every event recorded for them</b>. */
    public void delete(String userId) {
        http.request("DELETE", "/users/" + HttpCore.encode(Support.requireId(userId, "userId")), Map.of(), null, true);
    }

    /** Batch upsert (1-1000). ({@code import} is a Java keyword.) */
    public BatchResult importUsers(List<UserImport> users) {
        if (users == null || users.isEmpty()) {
            throw new ValidationException("users must be a non-empty list");
        }
        ArrayNode array = HttpCore.mapper().createArrayNode();
        for (UserImport u : users) {
            ObjectNode entry = Support.object();
            entry.put("user_id", Support.requireId(u.userId(), "userId"));
            Support.putProperties(entry, u.properties());
            array.add(entry);
        }
        ObjectNode body = Support.object();
        body.set("users", array);
        return Wire.batch(http.request("POST", "/users/import", Map.of(), body, true).data());
    }
}
