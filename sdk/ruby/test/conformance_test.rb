# frozen_string_literal: true

require_relative "test_helper"

# Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK runs, and that
# validate_against_openapi.py checks against the OpenAPI document.
class ConformanceTest < Minitest::Test
  include Helpers

  FILE = JSON.parse(File.read(File.expand_path("../../conformance/scenarios.json", __dir__)))
  DEFAULT = FILE["defaults"]

  def self.snake(name) = name.gsub(/([A-Z])/) { "_#{Regexp.last_match(1).downcase}" }

  # Scenario args are camelCase JSON. Only the *top-level* keys of option hashes (and of list entries) become
  # snake_case keywords - never anything inside +properties+, whose keys belong to the developer.
  def self.convert_args(args)
    positional = []
    keywords = {}
    args.each do |arg|
      case arg
      when Hash then keywords.merge!(snake_keys(arg))
      when Array then positional << arg.map { |e| e.is_a?(Hash) ? snake_keys(e) : e }
      else positional << arg
      end
    end
    [positional, keywords]
  end

  def self.snake_keys(hash) = hash.to_h { |k, v| [snake(k).to_sym, v] }

  def pick(obj, path)
    path.split(".").each do |key|
      return nil if obj.nil?

      obj = case obj
            when Hash then obj[key] # `properties`: the developer's own keys, never renamed
            when Array then key == "length" ? obj.length : obj[Integer(key)]
            else obj.public_send(self.class.snake(key))
            end
    end
    obj
  end

  FILE["scenarios"].each do |scenario|
    define_method("test_#{scenario["id"]}") do
      step = { status: scenario["response"]["status"], headers: scenario["response"]["headers"], body: scenario["response"]["body"] }
      options = { base_url: DEFAULT["baseUrl"], max_retries: 0 }
      options[:catalog] = scenario.dig("config", "catalog") if scenario.dig("config", "catalog")
      h = harness([step], **options)

      positional, keywords = self.class.convert_args(scenario["call"]["args"])
      resource = h.client.public_send(scenario["call"]["resource"])
      method = self.class.snake(scenario["call"]["method"])

      if scenario["error"]
        want = scenario["error"]
        error = assert_raises(Likyly::Error) { resource.public_send(method, *positional, **keywords) }
        assert_equal want["class"], error.class.name.split("::").last
        assert_equal want["statusCode"], error.status_code
        assert_equal want["requestId"], error.request_id if want["requestId"]
        assert_equal want["retryAfter"], error.retry_after if want["retryAfter"]
      else
        result = resource.public_send(method, *positional, **keywords)
        (scenario["expect"] || {}).each { |path, want| assert_equal want, pick(result, path), "result.#{path}" }
      end

      assert_request(scenario, h)
    end
  end

  private

  def assert_request(scenario, h)
    assert_equal 1, h.calls.size, "exactly one HTTP request"
    call = h.calls.first
    want = scenario["request"]
    assert_equal want["method"], call.method
    assert_equal DEFAULT["baseUrl"], "#{call.uri.scheme}://#{call.uri.host}"
    assert_equal want["path"], call.path, "raw request path"
    assert_equal want["query"] || {}, call.query, "query string"
    assert_equal DEFAULT["apiKey"], call.headers["X-API-Key"]
    assert call.headers["User-Agent"].start_with?(DEFAULT["userAgentPrefix"]), call.headers["User-Agent"]
    expected_type = want.key?("body") ? "application/json" : nil
    expected_type ? assert_equal(expected_type, call.headers["Content-Type"]) : assert_nil(call.headers["Content-Type"])
    if want["body"].nil?
      assert_nil call.body, "no request body"
    else
      assert_equal want["body"], JSON.parse(call.body), "request body"
    end
  end
end
