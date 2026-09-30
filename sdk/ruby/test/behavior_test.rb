# frozen_string_literal: true

require_relative "test_helper"

class ConfigurationTest < Minitest::Test
  include Helpers

  def test_requires_an_api_key
    ["", "  ", nil, 42].each { |key| assert_raises(Likyly::ValidationError) { Likyly::Client.new(api_key: key) } }
  end

  def test_defaults_to_production_and_strips_trailing_slashes
    t = FakeTransport.new([{ body: [] }])
    Likyly::Client.new(api_key: "k", transport: t).items.list
    assert_equal "api.likyly.com", t.calls[0].uri.host

    t = FakeTransport.new([{ body: [] }])
    Likyly::Client.new(api_key: "k", base_url: "https://proxy.example.test/likyly///", transport: t).items.list
    assert_equal "/likyly/items", t.calls[0].path
  end

  def test_user_agent_is_extended_not_replaced
    h = harness([{ body: [] }], user_agent: "my-shop/1.4")
    h.client.items.list
    assert_match(%r{\Alikyly-ruby/\d+\.\d+\.\d+ ruby/\d+\.\d+\.\d+ my-shop/1\.4\z}, h.calls[0].headers["User-Agent"])
  end

  def test_the_api_key_goes_in_the_header_never_the_url
    h = harness([{ body: [] }])
    h.client.items.list
    assert_equal "sk_test_conformance", h.calls[0].headers["X-API-Key"]
    refute_includes h.calls[0].url, "sk_test"
  end

  def test_timeout_is_passed_to_the_transport_and_overridable_per_call
    h = harness([{ body: [] }], timeout: 3)
    h.client.items.list
    h.client.items.list(timeout: 1.5)
    assert_equal [3, 1.5], h.calls.map(&:timeout)
  end
end

class ValidationTest < Minitest::Test
  include Helpers

  def test_validation_happens_before_anything_is_sent
    h = harness([{ body: EVT }])
    c = h.client
    {
      "event without a user or a session" => -> { c.events.view(item_id: "a") },
      "event without an item" => -> { c.events.view(user_id: "u") },
      "blank item id" => -> { c.events.view(item_id: "  ", user_id: "u") },
      "an Integer id is never coerced" => -> { c.events.view(item_id: 42, user_id: "u") },
      "a Symbol id is refused" => -> { c.items.get(:a) },
      "blank id on get" => -> { c.items.get("") },
      "item without a title" => -> { c.items.upsert("a") },
      "empty batch" => -> { c.items.upsert_many([]) },
      "empty delete batch" => -> { c.items.delete_many([]) },
      "empty user batch" => -> { c.users.import([]) },
      "empty event batch" => -> { c.events.track_many([]) },
      "unknown field in a batch event" => -> { c.events.track_many([{ type: "view", item_id: "a", user_id: "u", colour: "x" }]) },
      "bad event type" => -> { c.events.track("bad type!", user_id: "u", item_id: "i") },
      "zero limit" => -> { c.recommendations.popular(limit: 0) },
      "session with nothing" => -> { c.recommendations.session },
      "session with both" => -> { c.recommendations.session(viewed_item_ids: ["a"], user_id: "u") },
      "session with an empty list" => -> { c.recommendations.session(viewed_item_ids: []) },
      "session id with a comma" => -> { c.recommendations.session(viewed_item_ids: ["a,b"]) }
    }.each do |name, action|
      assert_raises(Likyly::ValidationError, name, &action)
    end
    assert_empty h.calls
  end

  def test_event_types_are_open_strings_but_url_safe
    h = harness([{ body: EVT }])
    h.client.events.track("favorite", user_id: "u", item_id: "i")
    assert_equal "/events/favorite", h.calls[0].path
  end

  def test_advanced_endpoints_refuse_an_id_containing_a_slash
    h = harness([{ body: REC }])
    error = assert_raises(Likyly::ValidationError) { h.client.recommendations.similar(item_id: "gid://shopify/Product/1") }
    assert_includes error.message, "recommendations.get"
    h.client.recommendations.get(item_id: "gid://shopify/Product/1") # the body-based call accepts it
    assert_equal 1, h.calls.size
  end

  def test_occurred_at_time_is_sent_as_utc_iso8601
    h = harness([{ body: EVT }])
    h.client.events.view(user_id: "u", item_id: "i", occurred_at: Time.new(2026, 9, 24, 12, 30, 0, "+02:00"))
    assert_equal "2026-09-24T10:30:00.000Z", h.calls[0].json["occurred_at"]
  end

  def test_properties_keys_are_never_renamed_at_any_depth
    h = harness([{ body: EVT }])
    props = { "orderId" => "O-1", orderRef: "R", "nested" => { "snakeCase_and_camelCase" => 1 } }
    h.client.events.purchase(user_id: "u", item_id: "i", properties: props)
    assert_equal({ "orderId" => "O-1", "orderRef" => "R", "nested" => { "snakeCase_and_camelCase" => 1 } }, h.calls[0].json["properties"])
  end

  def test_ids_are_encoded_as_a_single_segment
    h = harness([{ body: ITEM }])
    h.client.items.get("a b/c?d#e%f é")
    assert_equal "/items/a%20b%2Fc%3Fd%23e%25f%20%C3%A9", h.calls[0].path
  end

  def test_alpha_zero_is_sent_not_dropped
    h = harness([{ body: REC }])
    h.client.recommendations.hybrid(user_id: "u", item_id: "i", alpha: 0)
    assert_equal "0", h.calls[0].query["alpha"]
  end

  def test_debug_and_session_are_sent_on_get
    h = harness([{ body: REC }])
    h.client.recommendations.get(session_id: "s", debug: true, limit: 3)
    assert_equal({ "session_id" => "s", "count" => 3, "debug" => true }, h.calls[0].json)
  end

  def test_an_empty_get_sends_an_empty_object
    h = harness([{ body: REC }])
    h.client.recommendations.get
    assert_equal({}, h.calls[0].json)
  end
end

class ErrorsTest < Minitest::Test
  include Helpers

  def test_hierarchy_is_usable
    h = harness([{ status: 401, body: { "detail" => "nope", "request_id" => "req_x" } }], max_retries: 0)
    error = assert_raises(Likyly::AuthenticationError) { h.client.items.get("a") }
    assert_kind_of Likyly::ApiError, error
    assert_kind_of Likyly::Error, error
    assert_kind_of StandardError, error
    assert_equal "req_x", error.request_id
    assert_includes error.message, "req_x"
  end

  def test_each_status_maps_to_its_class
    { 403 => Likyly::PermissionDeniedError, 404 => Likyly::NotFoundError, 422 => Likyly::ValidationError,
      429 => Likyly::RateLimitError, 500 => Likyly::ApiError }.each do |status, klass|
      h = harness([{ status: status, body: { "detail" => "x" } }], max_retries: 0)
      error = assert_raises(Likyly::Error) { h.client.items.get("a") }
      assert_instance_of klass, error, "status #{status}"
      assert_equal status, error.status_code
    end
  end

  def test_a_422_lists_the_offending_fields_in_the_message
    h = harness([{ status: 422, body: { "detail" => [{ "loc" => %w[body title], "msg" => "Field required" }], "request_id" => "r" } }], max_retries: 0)
    assert_includes assert_raises(Likyly::ValidationError) { h.client.items.get("a") }.message, "Field required"
  end

  def test_a_non_json_error_body_still_becomes_an_api_error
    h = harness([{ status: 502, raw: "<html>Bad gateway</html>" }], max_retries: 0)
    error = assert_raises(Likyly::ApiError) { h.client.items.get("a") }
    assert_equal 502, error.status_code
    assert_equal "<html>Bad gateway</html>", error.body
  end

  def test_a_non_json_success_body_is_an_api_error
    h = harness([{ raw: "<html>captive portal</html>" }], max_retries: 0)
    assert_raises(Likyly::ApiError) { h.client.items.get("a") }
  end

  def test_network_failures_become_network_error_with_the_cause
    cause = Errno::ECONNRESET.new
    h = harness([{ fail: cause }], max_retries: 0)
    error = assert_raises(Likyly::NetworkError) { h.client.items.get("a") }
    assert_same cause, error.cause
    assert_raises(Likyly::NetworkError) { harness([{ fail: SocketError.new("getaddrinfo") }], max_retries: 0).client.items.get("a") }
  end

  def test_timeouts_become_timeout_error
    [Net::ReadTimeout.new, Net::OpenTimeout.new, Timeout::Error.new].each do |cause|
      h = harness([{ fail: cause }], max_retries: 0)
      assert_raises(Likyly::TimeoutError) { h.client.items.get("a") }
    end
  end

  def test_a_programming_error_in_a_custom_transport_is_not_swallowed
    h = harness([{ fail: NoMethodError.new("boom") }], max_retries: 0)
    assert_raises(NoMethodError) { h.client.items.get("a") }
  end

  def test_retry_after_accepts_an_http_date
    date = (Time.now + 30).httpdate
    h = harness([{ status: 429, headers: { "Retry-After" => date }, body: { "detail" => "x" } }], max_retries: 0)
    error = assert_raises(Likyly::RateLimitError) { h.client.items.get("a") }
    assert_in_delta 30, error.retry_after, 2
  end
end

class RetriesTest < Minitest::Test
  include Helpers

  def test_a_429_is_retried_for_every_request_honoring_retry_after
    h = harness([{ status: 429, headers: { "Retry-After" => "3" }, body: { "detail" => "slow down" } }, { body: EVT }])
    result = h.client.events.view(user_id: "u", item_id: "i") # no event_id: still safe, a 429 never reached the app
    refute_predicate result, :duplicate?
    assert_equal 2, h.calls.size
    assert_equal [3.0], h.sleeps
  end

  def test_a_retry_after_of_zero_means_retry_now
    h = harness([{ status: 429, headers: { "Retry-After" => "0" }, body: { "detail" => "x" } }, { body: ITEM }])
    h.client.items.get("a")
    assert_equal [0.0], h.sleeps
  end

  def test_a_429_without_retry_after_uses_backoff
    h = harness([{ status: 429, body: { "detail" => "x" } }, { body: ITEM }])
    h.client.items.get("a")
    assert_equal [0.5], h.sleeps
  end

  def test_a_retry_after_beyond_a_minute_is_not_waited_for
    h = harness([{ status: 429, headers: { "Retry-After" => "600" }, body: { "detail" => "x" } }])
    error = assert_raises(Likyly::RateLimitError) { h.client.items.get("a") }
    assert_equal 600, error.retry_after
    assert_equal 1, h.calls.size
  end

  def test_exponential_backoff_with_jitter_then_gives_up
    h = harness([DOWN], max_retries: 3)
    assert_raises(Likyly::ApiError) { h.client.items.get("a") }
    assert_equal 4, h.calls.size
    assert_equal [0.5, 1.0, 2.0], h.sleeps # random pinned to 1
  end

  def test_backoff_is_capped_at_eight_seconds
    h = harness([DOWN], max_retries: 7)
    assert_raises(Likyly::ApiError) { h.client.items.get("a") }
    assert_equal 8.0, h.sleeps.last
  end

  def test_the_default_jitter_is_random_between_zero_and_the_cap
    t = FakeTransport.new([DOWN])
    sleeps = []
    Likyly::Client.new(api_key: "k", transport: t, sleeper: ->(s) { sleeps << s }, max_retries: 2).items.get("a") rescue nil # rubocop:disable Style/RescueModifier
    assert_equal 2, sleeps.size
    assert(sleeps.each_with_index.all? { |s, i| s >= 0 && s <= 0.5 * (2**i) })
  end

  def test_idempotent_calls_are_retried_on_502_503_504
    [502, 503, 504].each do |status|
      h = harness([{ status: status, body: { "detail" => "x" } }, { body: ITEM }])
      h.client.items.upsert("a", title: "t")
      assert_equal 2, h.calls.size, "status #{status}"
      assert_equal h.calls[0].body, h.calls[1].body, "the replay must carry the same body"
    end
  end

  def test_an_event_without_an_event_id_is_never_retried_on_an_ambiguous_failure
    [DOWN, { fail: SocketError.new("reset") }, { fail: Net::ReadTimeout.new }].each do |first|
      h = harness([first, { body: EVT }])
      assert_raises(Likyly::Error) { h.client.events.purchase(user_id: "u", item_id: "i") }
      assert_equal 1, h.calls.size, "a duplicate purchase could have been recorded"
    end
  end

  def test_an_event_with_an_event_id_is_retried
    h = harness([DOWN, { body: { "message" => "ok", "event_id" => "e1", "duplicate" => true } }])
    result = h.client.events.purchase(event_id: "e1", user_id: "u", item_id: "i")
    assert_equal 2, h.calls.size
    assert_predicate result, :duplicate?
    assert_equal "e1", result.event_id
  end

  def test_track_many_is_retried_only_if_every_event_has_an_event_id
    batch = { "received" => 2, "accepted" => 2, "duplicates" => 0 }

    a = harness([DOWN, { body: batch }])
    assert_raises(Likyly::ApiError) do
      a.client.events.track_many([{ type: "view", user_id: "u", item_id: "1" }, { type: "view", user_id: "u", item_id: "2", event_id: "e" }])
    end
    assert_equal 1, a.calls.size

    b = harness([DOWN, { body: batch }])
    b.client.events.track_many([{ type: "view", user_id: "u", item_id: "1", event_id: "e1" }, { type: "view", user_id: "u", item_id: "2", event_id: "e2" }])
    assert_equal 2, b.calls.size
  end

  def test_client_errors_are_never_retried
    [400, 401, 403, 404, 422].each do |status|
      h = harness([{ status: status, body: { "detail" => "x" } }, { body: {} }])
      assert_raises(Likyly::Error) { h.client.items.get("a") }
      assert_equal 1, h.calls.size, "status #{status}"
    end
  end

  def test_per_call_max_retries_overrides_the_clients
    h = harness([DOWN], max_retries: 5)
    assert_raises(Likyly::ApiError) { h.client.items.get("a", max_retries: 0) }
    assert_equal 1, h.calls.size
  end
end

class ListsTest < Minitest::Test
  include Helpers

  def test_reports_the_total_and_the_pagination_used
    h = harness([{ body: [{ "item_id" => "a", "title" => "A" }], headers: { "X-Total-Count" => "42" } }])
    page = h.client.items.list(limit: 1, offset: 10)
    assert_equal [42, 1, 10, 1], [page.total, page.limit, page.offset, page.items.size]
    assert_equal({ "limit" => "1", "offset" => "10" }, h.calls[0].query)
  end

  def test_sends_only_what_you_asked_for_and_total_is_nil_when_unreported
    h = harness([{ body: [] }])
    page = h.client.users.list
    assert_equal({}, h.calls[0].query)
    assert_nil page.total
    assert_nil page.limit
    assert_equal 0, page.offset
    assert_empty page.users
  end

  def test_missing_optional_response_fields_are_nil_and_properties_default_to_a_hash
    h = harness([{ body: { "recommendation_id" => "r", "strategy" => "popular", "items" => [{ "item_id" => "a" }] } }])
    item = h.client.recommendations.get.items.first
    assert_equal "a", item.item_id
    assert_nil item.score
    assert_nil item.explanation
    assert_equal({}, item.properties)
  end
end
