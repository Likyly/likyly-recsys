# frozen_string_literal: true

module Likyly
  # Every error the SDK raises is a Likyly::Error.
  #
  #   Likyly::Error
  #   ├── ValidationError          invalid request - caught by the SDK before sending, or a 422 from the API
  #   ├── NetworkError             no HTTP response (DNS, connection reset, ...)
  #   ├── TimeoutError             the request timed out
  #   └── ApiError                 the API answered with an error status
  #       ├── AuthenticationError  401 - missing / invalid / revoked API key
  #       ├── PermissionDeniedError 403 - e.g. a public key used for a secret-key operation, or a plan limit
  #       ├── NotFoundError        404
  #       └── RateLimitError       429 - see #retry_after
  class Error < StandardError
    # @return [Integer, nil] the HTTP status code, when the API answered
    attr_reader :status_code
    # @return [String, nil] the request_id of the failed call (also the X-Request-ID header) - quote it when reporting a problem
    attr_reader :request_id
    # @return [Numeric, nil] the Retry-After header in seconds (429/503), when present
    attr_reader :retry_after
    # @return [Object, nil] the parsed response body (Hash, Array or String), when there was one
    attr_reader :body

    def initialize(message = nil, status_code: nil, request_id: nil, retry_after: nil, body: nil, cause: nil)
      super(message)
      @status_code = status_code
      @request_id = request_id
      @retry_after = retry_after
      @body = body
      @cause_override = cause
    end

    def message
      base = super
      request_id ? "#{base} (request_id=#{request_id})" : base
    end

    def cause
      @cause_override || super
    end
  end

  # Invalid request: caught by the SDK before anything is sent (status_code is nil), or a 422 from the API.
  class ValidationError < Error; end

  # No HTTP response: DNS failure, connection refused or reset, TLS error, ...
  class NetworkError < Error; end

  # The request timed out (the client's `timeout`).
  class TimeoutError < Error; end

  # The API answered with an error status.
  class ApiError < Error; end

  # 401: missing, invalid or revoked API key.
  class AuthenticationError < ApiError; end

  # 403: e.g. a public key used for a secret-key operation, or a plan limit.
  class PermissionDeniedError < ApiError; end

  # 404.
  class NotFoundError < ApiError; end

  # 429: see #retry_after.
  class RateLimitError < ApiError; end

  # @api private
  module Errors
    module_function

    def from_response(status, body, request_id, retry_after)
      klass = case status
              when 401 then AuthenticationError
              when 403 then PermissionDeniedError
              when 404 then NotFoundError
              when 422 then ValidationError
              when 429 then RateLimitError
              else ApiError
              end
      message = message_of(body) || "API error (HTTP #{status})"
      klass.new(message, status_code: status, request_id: request_id, retry_after: retry_after, body: body)
    end

    def message_of(body)
      return nil unless body.is_a?(Hash)

      detail = body["detail"]
      case detail
      when String then detail
      when Array then detail.map { |d| d.is_a?(Hash) && d["msg"] ? d["msg"].to_s : d.to_s }.join("; ")
      when nil then nil
      else detail.to_s
      end
    end
  end
end
