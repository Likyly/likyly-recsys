# frozen_string_literal: true

require "json"
require "net/http"
require "openssl"
require "time"
require "uri"

module Likyly
  # The default transport: one Net::HTTP connection per request (thread-safe, no shared state). Anything that
  # responds to +call(method, url, headers, body, timeout)+ and returns +[status, headers, body]+ (header names
  # lower-cased) can replace it - see the +transport:+ client option.
  class NetHttpTransport
    def call(method, url, headers, body, timeout)
      uri = URI.parse(url)
      http = Net::HTTP.new(uri.host, uri.port)
      http.use_ssl = uri.scheme == "https"
      http.open_timeout = http.read_timeout = http.write_timeout = timeout
      request = Net::HTTPGenericRequest.new(method, !body.nil?, true, uri.request_uri, headers)
      request.body = body if body
      response = http.request(request)
      [response.code.to_i, response.each_header.to_h { |k, v| [k.downcase, v] }, response.body.to_s.dup.force_encoding(Encoding::UTF_8)]
    end
  end

  # The only place that talks HTTP. Every resource goes through #request: auth header, catalog parameter,
  # timeout, retries with exponential backoff + jitter, and the error mapping.
  # @api private
  class HttpCore
    RETRY_BASE = 0.5
    RETRY_CAP = 8.0
    # A Retry-After longer than this is not waited for: the RateLimitError is raised instead.
    MAX_RETRY_AFTER = 60

    Response = Struct.new(:status, :headers, :body) do
      def json
        JSON.parse(body)
      rescue JSON::ParserError => e
        raise ApiError.new("the API answered with a body that is not the expected JSON: #{e.message}", status_code: status)
      end
    end

    def initialize(api_key:, base_url:, catalog:, timeout:, max_retries:, user_agent:, transport:, sleeper:, random:)
      @api_key = api_key
      @base_url = base_url.sub(%r{/+\z}, "")
      @catalog = catalog
      @timeout = timeout
      @max_retries = max_retries
      @user_agent = user_agent
      @transport = transport
      @sleeper = sleeper
      @random = random
    end

    # +idempotent+ says whether the call is safe to send again if its outcome is unknown (a timeout, a 5xx, a
    # dropped connection): false for events without an event_id, where a blind retry could record the event twice.
    def request(method, path, idempotent:, query: {}, body: nil, timeout: nil, max_retries: nil)
      timeout ||= @timeout
      max_retries ||= @max_retries
      url = build_url(path, query)
      payload = body.nil? ? nil : JSON.generate(body)
      attempt = 0
      begin
        perform(method, url, payload, timeout)
      rescue Error => e
        delay = retry_delay(e, attempt, idempotent)
        raise if delay.nil? || attempt >= max_retries

        @sleeper.call(delay)
        attempt += 1
        retry
      end
    end

    private

    def build_url(path, query)
      params = query.compact.map { |k, v| "#{Support.encode(k)}=#{Support.encode(v)}" }
      params << "data_product_type=#{Support.encode(@catalog)}" if @catalog
      "#{@base_url}#{path}#{params.empty? ? "" : "?#{params.join("&")}"}"
    end

    def perform(method, url, payload, timeout)
      headers = { "X-API-Key" => @api_key, "Accept" => "application/json", "User-Agent" => @user_agent }
      headers["Content-Type"] = "application/json" if payload
      begin
        status, response_headers, text = @transport.call(method, url, headers, payload, timeout)
      rescue ::Timeout::Error => e
        raise TimeoutError.new("request timed out after #{timeout}s", cause: e)
      rescue SocketError, SystemCallError, IOError, OpenSSL::SSL::SSLError => e
        raise NetworkError.new("could not reach the API: #{e.message}", cause: e)
      end

      return Response.new(status, response_headers, text) if status < 400

      body = begin
        text.to_s.empty? ? nil : JSON.parse(text)
      rescue JSON::ParserError
        text # e.g. an HTML 502 page from a proxy
      end
      request_id = response_headers["x-request-id"] || (body.is_a?(Hash) ? body["request_id"]&.to_s : nil)
      raise Errors.from_response(status, body, request_id, parse_retry_after(response_headers["retry-after"]))
    end

    def retry_delay(error, attempt, idempotent)
      backoff = @random.call * [RETRY_CAP, RETRY_BASE * (2**attempt)].min # full jitter
      case error
      when ApiError, ValidationError
        status = error.status_code
        return nil if status.nil?

        if status == 429
          # Rejected by the rate limiter before reaching the application: nothing was processed, so retrying is safe for every request.
          return after_header(error, backoff)
        end
        return nil unless idempotent # the outcome is unknown - never risk a duplicate

        [502, 503, 504].include?(status) ? after_header(error, backoff) : nil
      when NetworkError, TimeoutError
        idempotent ? backoff : nil
      end
    end

    def after_header(error, backoff)
      return backoff if error.retry_after.nil?

      error.retry_after <= MAX_RETRY_AFTER ? error.retry_after : nil
    end

    def parse_retry_after(value)
      return nil if value.nil? || value.strip.empty?
      return Float(value) if value.match?(/\A\s*\d+(\.\d+)?\s*\z/)

      [Time.httpdate(value) - Time.now, 0].max
    rescue ArgumentError
      nil
    end
  end
end
