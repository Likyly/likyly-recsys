package com.likyly.error;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/** Maps an HTTP error response to the right exception. */
public final class Errors {
    private Errors() {
    }

    public static LikylyException fromResponse(int status, Object body, String requestId, Double retryAfter) {
        String message = messageOf(body);
        if (message == null) {
            message = "LIKYLY API error (HTTP " + status + ")";
        }
        return switch (status) {
            case 401 -> new AuthenticationException(message, status, requestId, retryAfter, body, null);
            case 403 -> new PermissionDeniedException(message, status, requestId, retryAfter, body, null);
            case 404 -> new NotFoundException(message, status, requestId, retryAfter, body, null);
            case 422 -> new ValidationException(message, status, requestId, retryAfter, body, null);
            case 429 -> new RateLimitException(message, status, requestId, retryAfter, body, null);
            default -> new ApiException(message, status, requestId, retryAfter, body, null);
        };
    }

    private static String messageOf(Object body) {
        if (body instanceof Map<?, ?> map && map.containsKey("detail")) {
            Object detail = map.get("detail");
            if (detail instanceof String s) {
                return s;
            }
            if (detail instanceof List<?> list) {
                return list.stream()
                    .map(d -> d instanceof Map<?, ?> m && m.get("msg") != null ? String.valueOf(m.get("msg")) : String.valueOf(d))
                    .collect(Collectors.joining("; "));
            }
            return String.valueOf(detail);
        }
        return null;
    }
}
