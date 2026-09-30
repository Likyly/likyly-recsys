package likyly

import (
	"regexp"
	"strings"
)

var eventTypePattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// encodeSegment percent-encodes a path segment: everything outside RFC 3986 "unreserved"
// (A-Z a-z 0-9 - . _ ~) becomes %XX (UTF-8), '/' included - so an id like "gid://shopify/Product/1" is one segment.
func encodeSegment(s string) string {
	const hex = "0123456789ABCDEF"
	var sb strings.Builder
	for i := 0; i < len(s); i++ {
		b := s[i]
		if (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') || (b >= '0' && b <= '9') || b == '-' || b == '.' || b == '_' || b == '~' {
			sb.WriteByte(b)
		} else {
			sb.WriteByte('%')
			sb.WriteByte(hex[b>>4])
			sb.WriteByte(hex[b&15])
		}
	}
	return sb.String()
}

// requireID: ids are opaque, non-empty strings.
func requireID(value, name string) (string, error) {
	if strings.TrimSpace(value) == "" {
		return "", validationf(`%s must be a non-empty string (your own identifier, e.g. "SKU-123")`, name)
	}
	return value, nil
}

// requirePathSafeID: the advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used.
func requirePathSafeID(value, name string) (string, error) {
	id, err := requireID(value, name)
	if err != nil {
		return "", err
	}
	if strings.Contains(id, "/") {
		return "", validationf(`%s "%s" contains "/", which this endpoint cannot carry in its URL path - use Recommendations.Get, which takes ids in the request body`, name, id)
	}
	return id, nil
}

func requireEventType(t string) (string, error) {
	if !eventTypePattern.MatchString(t) {
		return "", validationf(`event type must be 1-64 characters of letters, digits, "_" or "-" (e.g. "view", "add_to_cart", "favorite")`)
	}
	return t, nil
}
