package com.likyly;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.likyly.error.LikylyException;
import com.likyly.http.Transport;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK runs, and
 * that validate_against_openapi.py checks against the OpenAPI document. Generic: it calls the SDK by
 * reflection (resource, method name, JSON arguments converted to the parameter types), so a new
 * scenario needs no Java code.
 */
class ConformanceTest {
    static final ObjectMapper MAPPER = new ObjectMapper();
    static JsonNode file;

    static Stream<JsonNode> scenarios() throws IOException {
        file = MAPPER.readTree(Files.readString(Path.of("..", "conformance", "scenarios.json")));
        List<JsonNode> all = new ArrayList<>();
        file.get("scenarios").forEach(all::add);
        return all.stream();
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("scenarios")
    void scenario(JsonNode scenario) throws Exception {
        JsonNode defaults = file.get("defaults");
        JsonNode response = scenario.get("response");
        Map<String, String> headers = new LinkedHashMap<>();
        response.get("headers").fields().forEachRemaining(e -> headers.put(e.getKey().toLowerCase(), e.getValue().asText()));
        String body = response.get("body").isNull() ? null : response.get("body").toString();
        FakeTransport transport = new FakeTransport(List.of(FakeTransport.Step.status(response.get("status").asInt(), headers, body)));

        Likyly.Builder builder = Likyly.builder().apiKey(defaults.get("apiKey").asText()).baseUrl(defaults.get("baseUrl").asText())
            .transport(transport).maxRetries(0);
        if (scenario.has("config") && scenario.get("config").has("catalog")) {
            builder.catalog(scenario.get("config").get("catalog").asText());
        }
        Likyly client = builder.build();

        JsonNode call = scenario.get("call");
        Object resource = Likyly.class.getMethod(call.get("resource").asText()).invoke(client);
        Method method = find(resource.getClass(), javaName(call.get("resource").asText(), call.get("method").asText()), call.get("args").size());
        Object[] args = convertArgs(method, call.get("args"));

        if (scenario.has("error")) {
            JsonNode want = scenario.get("error");
            LikylyException e = assertThrows(LikylyException.class, () -> invoke(method, resource, args));
            assertEquals(want.get("class").asText().replace("Error", "Exception"), e.getClass().getSimpleName());
            assertEquals(want.get("statusCode").asInt(), e.statusCode());
            if (want.has("requestId")) {
                assertEquals(want.get("requestId").asText(), e.requestId());
            }
            if (want.has("retryAfter")) {
                assertEquals(want.get("retryAfter").asDouble(), e.retryAfter());
            }
            if (want.has("message")) {
                assertEquals(want.get("message").asText(), e.getMessage());
            }
        } else {
            JsonNode result = MAPPER.valueToTree(invoke(method, resource, args));
            if (scenario.has("expect")) {
                for (Iterator<Map.Entry<String, JsonNode>> it = scenario.get("expect").fields(); it.hasNext();) {
                    Map.Entry<String, JsonNode> want = it.next();
                    assertJson(want.getValue(), pick(result, want.getKey()), "result." + want.getKey());
                }
            }
        }
        assertRequest(scenario, transport, defaults);
    }

    /** {@code import} is a Java keyword: the SDK calls it importItems / importUsers. */
    private static String javaName(String resource, String method) {
        if (method.equals("import")) {
            return resource.equals("items") ? "importItems" : "importUsers";
        }
        return method;
    }

    private static Method find(Class<?> type, String name, int argc) {
        return java.util.Arrays.stream(type.getMethods())
            .filter(m -> m.getName().equals(name) && m.getParameterCount() == argc)
            .findFirst().orElseThrow(() -> new AssertionError(type.getSimpleName() + "." + name + "/" + argc + " does not exist"));
    }

    private static Object[] convertArgs(Method method, JsonNode args) {
        Object[] out = new Object[args.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = MAPPER.convertValue(args.get(i), MAPPER.constructType(method.getGenericParameterTypes()[i]));
        }
        return out;
    }

    private static Object invoke(Method method, Object target, Object[] args) throws Exception {
        try {
            return method.invoke(target, args);
        } catch (InvocationTargetException e) {
            if (e.getCause() instanceof Exception ex) {
                throw ex;
            }
            throw e;
        }
    }

    private static JsonNode pick(JsonNode node, String path) {
        for (String key : path.split("\\.")) {
            if (node == null || node.isNull()) {
                return null;
            }
            if (key.equals("length")) {
                return MAPPER.valueToTree(node.size());
            }
            node = key.chars().allMatch(Character::isDigit) ? node.get(Integer.parseInt(key)) : node.get(key);
        }
        return node;
    }

    private static void assertJson(JsonNode want, JsonNode got, String where) {
        assertNotNull(got, where + " is missing");
        if (want.isNumber() && got.isNumber()) {
            assertEquals(want.asDouble(), got.asDouble(), 1e-9, where);
        } else {
            assertEquals(want, got, where);
        }
    }

    private static void assertRequest(JsonNode scenario, FakeTransport transport, JsonNode defaults) throws IOException {
        assertEquals(1, transport.calls.size(), "exactly one HTTP request");
        Transport.Request request = transport.calls.get(0);
        JsonNode want = scenario.get("request");
        assertEquals(want.get("method").asText(), request.method());
        assertEquals(defaults.get("baseUrl").asText(), request.uri().getScheme() + "://" + request.uri().getRawAuthority());
        assertEquals(want.get("path").asText(), request.uri().getRawPath(), "raw request path");

        Map<String, String> query = new LinkedHashMap<>();
        String raw = request.uri().getRawQuery();
        if (raw != null) {
            for (String pair : raw.split("&")) {
                String[] kv = pair.split("=", 2);
                query.put(URLDecoder.decode(kv[0], StandardCharsets.UTF_8), URLDecoder.decode(kv[1], StandardCharsets.UTF_8));
            }
        }
        Map<String, String> wantQuery = new LinkedHashMap<>();
        want.get("query").fields().forEachRemaining(e -> wantQuery.put(e.getKey(), e.getValue().asText()));
        assertEquals(wantQuery, query, "query string");

        assertEquals(defaults.get("apiKey").asText(), request.headers().get("X-API-Key"));
        assertTrue(request.headers().get("User-Agent").startsWith(defaults.get("userAgentPrefix").asText()), "user agent");
        assertEquals(want.has("body") ? "application/json" : null, request.headers().get("Content-Type"));
        if (want.has("body")) {
            assertEquals(want.get("body"), MAPPER.readTree(request.body()), "request body");
        } else {
            assertEquals(null, request.body());
        }
    }
}
