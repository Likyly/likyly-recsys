package likyly

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
)

// Every error the SDK returns is one of the types below; test them with errors.As:
//
//	var rl *likyly.RateLimitError
//	if errors.As(err, &rl) { time.Sleep(rl.RetryAfter) }
//
// Hierarchy (an *AuthenticationError also matches *APIError):
//
//	*ValidationError          invalid request - caught by the SDK before sending, or a 422 from the API
//	*NetworkError             no HTTP response (DNS, connection reset, ...)
//	*TimeoutError             the request timed out
//	*APIError                 the API answered with an error status
//	  *AuthenticationError    401 - missing / invalid / revoked API key
//	  *PermissionDeniedError  403 - e.g. a public key used for a secret-key operation, or a plan limit
//	  *NotFoundError          404
//	  *RateLimitError         429 - see RetryAfter

// APIError is the API's answer to a failed call.
type APIError struct {
	// StatusCode is the HTTP status code.
	StatusCode int
	// Message is the API's explanation.
	Message string
	// RequestID is the request_id of the failed call (also the X-Request-ID header) - quote it when reporting a problem.
	RequestID string
	// RetryAfter is the Retry-After header (429/503); zero if absent.
	RetryAfter time.Duration
	// Body is the parsed response body (a map, slice or string), when there was one.
	Body any

	hasRetryAfter bool
}

func (e *APIError) Error() string {
	if e.RequestID != "" {
		return fmt.Sprintf("likyly: %s (HTTP %d, request_id=%s)", e.Message, e.StatusCode, e.RequestID)
	}
	return fmt.Sprintf("likyly: %s (HTTP %d)", e.Message, e.StatusCode)
}

// AuthenticationError is a 401: missing, invalid or revoked API key.
type AuthenticationError struct{ *APIError }

// PermissionDeniedError is a 403: e.g. a public key used for a secret-key operation, or a plan limit.
type PermissionDeniedError struct{ *APIError }

// NotFoundError is a 404.
type NotFoundError struct{ *APIError }

// RateLimitError is a 429; RetryAfter says how long to wait.
type RateLimitError struct{ *APIError }

// Unwrap lets errors.As find the embedded *APIError.
func (e *AuthenticationError) Unwrap() error { return e.APIError }

// Unwrap lets errors.As find the embedded *APIError.
func (e *PermissionDeniedError) Unwrap() error { return e.APIError }

// Unwrap lets errors.As find the embedded *APIError.
func (e *NotFoundError) Unwrap() error { return e.APIError }

// Unwrap lets errors.As find the embedded *APIError.
func (e *RateLimitError) Unwrap() error { return e.APIError }

// ValidationError is an invalid request: caught by the SDK before sending (API is nil), or a 422 from the API.
type ValidationError struct {
	Message string
	// API is set when the error came from a 422 response.
	API *APIError
}

func (e *ValidationError) Error() string {
	if e.API != nil {
		return e.API.Error()
	}
	return "likyly: " + e.Message
}

// Unwrap exposes the API error of a 422, if any.
func (e *ValidationError) Unwrap() error {
	if e.API == nil {
		return nil
	}
	return e.API
}

// NetworkError means there was no HTTP response: DNS, connection reset, ...
type NetworkError struct{ Err error }

func (e *NetworkError) Error() string { return "likyly: could not reach the API: " + e.Err.Error() }

// Unwrap returns the underlying error.
func (e *NetworkError) Unwrap() error { return e.Err }

// TimeoutError means the request timed out (the client's timeout, not your context's cancellation).
type TimeoutError struct {
	Timeout time.Duration
	Err     error
}

func (e *TimeoutError) Error() string {
	return fmt.Sprintf("likyly: request timed out after %s", e.Timeout)
}

// Unwrap returns the underlying error.
func (e *TimeoutError) Unwrap() error { return e.Err }

// IsTimeout reports whether the error is a timeout (net.Error compatibility).
func (e *TimeoutError) IsTimeout() bool { return true }

func validationf(format string, args ...any) error {
	return &ValidationError{Message: fmt.Sprintf(format, args...)}
}

func newAPIError(status int, body any, requestID string, retryAfter time.Duration, hasRetryAfter bool) error {
	base := &APIError{StatusCode: status, Message: messageOf(body), RequestID: requestID, RetryAfter: retryAfter, Body: body, hasRetryAfter: hasRetryAfter}
	if base.Message == "" {
		base.Message = fmt.Sprintf("API error (HTTP %d)", status)
	}
	switch status {
	case 401:
		return &AuthenticationError{base}
	case 403:
		return &PermissionDeniedError{base}
	case 404:
		return &NotFoundError{base}
	case 422:
		return &ValidationError{Message: base.Message, API: base}
	case 429:
		return &RateLimitError{base}
	default:
		return base
	}
}

func messageOf(body any) string {
	m, ok := body.(map[string]any)
	if !ok {
		return ""
	}
	switch d := m["detail"].(type) {
	case string:
		return d
	case []any:
		parts := make([]string, 0, len(d))
		for _, item := range d {
			if im, ok := item.(map[string]any); ok && im["msg"] != nil {
				parts = append(parts, fmt.Sprint(im["msg"]))
			} else {
				parts = append(parts, fmt.Sprint(item))
			}
		}
		return strings.Join(parts, "; ")
	case nil:
		return ""
	default:
		return fmt.Sprint(d)
	}
}

func isContextError(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}
