package com.likyly;

import com.likyly.http.Transport;
import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Replays scripted responses in order (the last one repeats) and records every request. */
final class FakeTransport implements Transport {
    /** One scripted step: a response, or an exception to throw. */
    record Step(int status, Map<String, String> headers, String body, IOException failure) {
        static Step ok(String body) {
            return new Step(200, Map.of(), body, null);
        }

        static Step status(int status, String body) {
            return new Step(status, Map.of(), body, null);
        }

        static Step status(int status, Map<String, String> headers, String body) {
            return new Step(status, headers, body, null);
        }

        static Step fail(IOException e) {
            return new Step(0, Map.of(), null, e);
        }
    }

    final List<Request> calls = new ArrayList<>();
    private final List<Step> script;
    private int index;

    FakeTransport(List<Step> script) {
        this.script = script;
    }

    @Override
    public Response send(Request request, Duration timeout) throws IOException {
        calls.add(request);
        Step step = script.get(Math.min(index++, script.size() - 1));
        if (step.failure() != null) {
            throw step.failure();
        }
        return new Response(step.status(), step.headers(), step.body());
    }
}
