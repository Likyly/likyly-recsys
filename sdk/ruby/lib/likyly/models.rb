# frozen_string_literal: true

module Likyly
  # Free-form properties (price, currency, orderId, category, ...) are plain Hashes whose keys are yours: the SDK
  # never renames them. Hashes you read back have String keys, exactly as stored.

  # A catalog entry. +item_id+ is your own identifier - any string (SKU-123, a UUID, gid://shopify/Product/123).
  Item = Struct.new(:item_id, :title, :description, :properties, keyword_init: true)

  # One page of the catalog. +total+ is the whole catalog's size (X-Total-Count), nil if the API did not report
  # it; +limit+ is the page size you asked for (nil = the API's default).
  ItemList = Struct.new(:items, :total, :limit, :offset, keyword_init: true)

  # An optional user profile.
  User = Struct.new(:user_id, :properties, keyword_init: true)

  # One page of users.
  UserList = Struct.new(:users, :total, :limit, :offset, keyword_init: true)

  # One failed entry of a batch call.
  BatchError = Struct.new(:index, :id, :message, keyword_init: true)

  # Result of a batch call. A failing entry is reported in +errors+, it does not raise.
  BatchResult = Struct.new(:received, :succeeded, :failed, :errors, keyword_init: true)

  # Answer to a recorded event. +duplicate+ is true if this +event_id+ was already recorded - nothing was written.
  EventResult = Struct.new(:message, :event_id, :duplicate, keyword_init: true) do
    def duplicate?
      duplicate == true
    end
  end

  # Answer to events.track_many. +duplicates+ are events skipped because their +event_id+ was already recorded.
  EventBatchResult = Struct.new(:received, :accepted, :duplicates, keyword_init: true)

  # A user whose history contributed to a recommendation (debug, secret key only).
  SimilarUser = Struct.new(:user_id, :shared_item_ids, keyword_init: true)

  # Why an item was recommended.
  Explanation = Struct.new(
    :reason, :content_similarity, :semantic_similarity, :popularity_score, :interaction_count,
    :interaction_label, :collaborative_score, :source_item_ids, :similar_users,
    keyword_init: true
  )

  # One recommended item.
  RecommendedItem = Struct.new(:item_id, :score, :title, :description, :properties, :explanation, keyword_init: true)

  # What every recommendation call returns. Send +recommendation_id+ back on the impression / click /
  # add_to_cart / purchase events for these items. +strategy+ is what LIKYLY used: hybrid, content,
  # collaborative, session or popular.
  RecommendationResponse = Struct.new(:recommendation_id, :strategy, :placement, :items, keyword_init: true)

  # @api private
  module Wire
    module_function

    def item(w)
      Item.new(item_id: w["item_id"], title: w["title"], description: w["description"], properties: w["properties"] || {})
    end

    def user(w)
      User.new(user_id: w["user_id"], properties: w["properties"] || {})
    end

    def batch(w)
      errors = (w["errors"] || []).map { |e| BatchError.new(index: e["index"], id: e["id"], message: e["message"]) }
      BatchResult.new(received: w["received"], succeeded: w["succeeded"], failed: w["failed"], errors: errors)
    end

    def explanation(w)
      Explanation.new(
        reason: w["reason"], content_similarity: w["content_similarity"], semantic_similarity: w["semantic_similarity"],
        popularity_score: w["popularity_score"], interaction_count: w["interaction_count"],
        interaction_label: w["interaction_label"], collaborative_score: w["collaborative_score"],
        source_item_ids: w["source_item_ids"],
        similar_users: w["similar_users"]&.map { |u| SimilarUser.new(user_id: u["user_id"], shared_item_ids: u["shared_item_ids"] || []) }
      )
    end

    def recommendation(w)
      items = w["items"].map do |i|
        RecommendedItem.new(
          item_id: i["item_id"], score: i["score"], title: i["title"], description: i["description"],
          properties: i["properties"] || {}, explanation: i["explanation"] ? explanation(i["explanation"]) : nil
        )
      end
      RecommendationResponse.new(recommendation_id: w["recommendation_id"], strategy: w["strategy"], placement: w["placement"], items: items)
    end
  end
end
