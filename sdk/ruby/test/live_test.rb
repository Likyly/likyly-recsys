# frozen_string_literal: true

require_relative "test_helper"

# End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless LIKYLY_TEST_URL,
# LIKYLY_TEST_SECRET_KEY and LIKYLY_TEST_PUBLIC_KEY are set. The tests share state and run in order.
class LiveTest < Minitest::Test
  URL = ENV.fetch("LIKYLY_TEST_URL", nil)
  SECRET = ENV.fetch("LIKYLY_TEST_SECRET_KEY", nil)
  PUBLIC = ENV.fetch("LIKYLY_TEST_PUBLIC_KEY", nil)
  CATALOG = "ruby-#{(Time.now.to_f * 1000).to_i}"
  GID = "gid://shopify/Product/123456"

  def setup
    skip "LIKYLY_TEST_* not set" unless URL && SECRET && PUBLIC
  end

  def server = Likyly::Client.new(api_key: SECRET, base_url: URL, catalog: CATALOG)
  def browser = Likyly::Client.new(api_key: PUBLIC, base_url: URL, catalog: CATALOG)

  def self.test_order = :alpha

  def test_1_items_upsert_get_list_delete_with_ids_of_any_shape
    likyly = server
    created = likyly.items.upsert("SKU-123", title: "Nike Air Max", description: "Running shoe",
                                             properties: { "category" => "shoes", "brand" => "Nike", "price" => 129.9 })
    assert_equal "SKU-123", created.item_id
    assert_equal({ "category" => "shoes", "brand" => "Nike", "price" => 129.9 }, created.properties)

    likyly.items.upsert(GID, title: "Shopify boot", description: "warm winter boot", properties: { "category" => "boots" })
    likyly.items.upsert("3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", title: "UUID shoe", description: "trail running shoe", properties: { "category" => "shoes" })
    assert_equal "Shopify boot", likyly.items.get(GID).title

    page = likyly.items.list(limit: 2, offset: 0)
    assert_equal 2, page.items.size
    assert_equal 3, page.total

    many = likyly.items.upsert_many([
                                      { item_id: "SKU-A", title: "Adidas", description: "road running shoe", properties: { "category" => "shoes" } },
                                      { item_id: "SKU-B", title: "Asics", description: "road running shoe", properties: { "category" => "shoes" } }
                                    ])
    assert_equal 2, many.succeeded

    likyly.items.delete("SKU-B")
    assert_raises(Likyly::NotFoundError) { likyly.items.get("SKU-B") }
    gone = likyly.items.delete_many(%w[SKU-A ghost])
    assert_equal [1, 1, "ghost"], [gone.succeeded, gone.failed, gone.errors.first.id]
    likyly.items.upsert("SKU-A", title: "Adidas", description: "road running shoe", properties: { "category" => "shoes" })
  end

  def test_2_users_upsert_get_list_delete_with_free_form_properties
    likyly = server
    user = likyly.users.upsert("user_123", properties: { "country" => "FR", "segment" => "premium", "language" => "fr" })
    assert_equal "user_123", user.user_id
    assert_equal "premium", likyly.users.get("user_123").properties["segment"]
    assert(likyly.users.list(limit: 10).users.any? { |u| u.user_id == "user_123" })
    assert_equal 1, likyly.users.import([{ user_id: "u_imp", properties: { "country" => "DE" } }]).succeeded
    likyly.users.delete("u_imp")
    assert_raises(Likyly::NotFoundError) { likyly.users.get("u_imp") }
  end

  def test_3_events_identified_anonymous_both_custom_idempotent_purchase_batch_with_the_public_key
    likyly = browser
    likyly.events.view(user_id: "user_123", item_id: "SKU-123")
    likyly.events.view(session_id: "sess_123", item_id: "SKU-123")
    likyly.events.view(user_id: "user_789", session_id: "sess_123", item_id: GID)
    likyly.events.track("favorite", user_id: "user_123", item_id: "SKU-123", properties: { "source" => "wishlist" })
    likyly.events.add_to_cart(user_id: "user_123", item_id: "SKU-123", quantity: 1)
    likyly.events.remove_from_cart(user_id: "user_123", item_id: "SKU-123", quantity: 1)
    purchase = { event_id: "purchase_#{(Time.now.to_f * 1000).to_i}", user_id: "user_123", item_id: "SKU-123", quantity: 1,
                 properties: { "price" => 129.9, "currency" => "EUR", "orderId" => "ORDER-9281" } }
    refute_predicate likyly.events.purchase(**purchase), :duplicate?
    assert_predicate likyly.events.purchase(**purchase), :duplicate?
    batch = likyly.events.track_many([{ type: "view", user_id: "user_123", item_id: "SKU-A" },
                                      { type: "click", session_id: "sess_123", item_id: "SKU-A" }])
    assert_equal [2, 2, 0], [batch.received, batch.accepted, batch.duplicates]
  end

  def test_4_recommendations_get_in_every_context_then_attribution_through_recommendation_id
    likyly = browser
    for_user = likyly.recommendations.get(user_id: "user_123", placement: "homepage", limit: 3)
    assert_match(/\Arec_[0-9A-Z]{26}\z/, for_user.recommendation_id)
    assert_equal "homepage", for_user.placement
    assert for_user.items.size.between?(1, 3)
    assert_equal "content", likyly.recommendations.get(item_id: "SKU-123", limit: 2).strategy
    assert_equal "session", likyly.recommendations.get(session_id: "sess_123", viewed_item_ids: ["SKU-123"], limit: 2).strategy
    assert_equal "content", likyly.recommendations.get(item_id: GID, limit: 2).strategy # an id with "/" in the body
    refute_nil likyly.recommendations.get(limit: 2).recommendation_id

    shown = for_user.items.first
    likyly.events.impression(user_id: "user_123", item_id: shown.item_id, recommendation_id: for_user.recommendation_id, placement: "homepage")
    click = likyly.events.click(user_id: "user_123", item_id: shown.item_id, recommendation_id: for_user.recommendation_id, placement: "homepage")
    refute_predicate click, :duplicate?
  end

  def test_5_advanced_recommendations_answer_with_the_same_response_type
    likyly = browser
    refute_nil likyly.recommendations.popular(limit: 2).recommendation_id
    similar = likyly.recommendations.similar(item_id: "SKU-123", limit: 2)
    assert_equal "content", similar.strategy
    refute_nil similar.items.first.item_id
    refute_nil likyly.recommendations.hybrid(user_id: "user_123", item_id: "SKU-123", limit: 2, alpha: 0.3).recommendation_id
    assert_equal "session", likyly.recommendations.session(viewed_item_ids: %w[SKU-123 SKU-A], limit: 2).strategy
    refute_nil likyly.recommendations.session(user_id: "user_123", limit: 2).recommendation_id
    assert_raises(Likyly::NotFoundError) { likyly.recommendations.collaborative(user_id: "user_123", limit: 2) } # no trained model yet
  end

  def test_6_the_public_key_cannot_manage_the_catalog_or_users_and_a_wrong_key_is_refused
    assert_raises(Likyly::PermissionDeniedError) { browser.items.upsert("x", title: "t") }
    assert_raises(Likyly::PermissionDeniedError) { browser.items.list }
    assert_raises(Likyly::PermissionDeniedError) { browser.users.get("user_123") }
    assert_raises(Likyly::AuthenticationError) { Likyly::Client.new(api_key: "nope", base_url: URL).events.view(user_id: "u", item_id: "i") }
  end

  def test_7_api_side_validation_surfaces_as_validation_error_with_the_request_id
    error = assert_raises(Likyly::ValidationError) { server.items.list(limit: 5000) }
    assert_equal 422, error.status_code
    assert_match(/\Areq_/, error.request_id)
  end

  def test_8_cleanup
    likyly = server
    likyly.items.delete_many(["SKU-123", GID, "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "SKU-A"])
    likyly.users.delete("user_123")
  end
end
