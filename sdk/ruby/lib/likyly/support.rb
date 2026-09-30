# frozen_string_literal: true

module Likyly
  # @api private
  module Support
    UNRESERVED = /[^A-Za-z0-9\-._~]/

    module_function

    # Percent-encodes a path segment: everything outside RFC 3986 "unreserved" (A-Z a-z 0-9 - . _ ~) becomes
    # %XX (UTF-8), '/' included - so an id like "gid://shopify/Product/1" is one segment.
    def encode(value)
      value.to_s.dup.force_encoding(Encoding::BINARY).gsub(UNRESERVED) { |c| format("%%%02X", c.ord) }
    end

    # Ids are opaque, non-empty strings - never coerced (an Integer or a Symbol is refused).
    def require_id(value, name)
      return value if value.is_a?(String) && !value.strip.empty?

      raise ValidationError, "#{name} must be a non-empty String (your own identifier, e.g. \"SKU-123\")"
    end

    # Advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used.
    def require_path_safe_id(value, name)
      id = require_id(value, name)
      if id.include?("/")
        raise ValidationError,
              "#{name} #{id.inspect} contains \"/\", which this endpoint cannot carry in its URL path - " \
              "use recommendations.get, which takes ids in the request body"
      end
      id
    end

    # Drops nil values so optional fields are simply not sent.
    def compact(hash)
      hash.reject { |_, v| v.nil? }
    end

    def limit_of(limit)
      limit = 10 if limit.nil?
      raise ValidationError, "limit must be a positive Integer" unless limit.is_a?(Integer) && limit >= 1

      limit
    end
  end
end
