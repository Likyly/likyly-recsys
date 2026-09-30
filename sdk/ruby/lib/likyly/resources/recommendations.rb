# frozen_string_literal: true

module Likyly
  # +client.recommendations+. Use {#get}: send what you know and LIKYLY picks the best strategy. The other methods
  # are the *Advanced Recommendations* - one strategy at a time.
  #
  # Every method also accepts +timeout:+ and +max_retries:+ to override the client's for that call.
  class Recommendations
    # @api private
    def initialize(http)
      @http = http
    end

    # Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at all
    # (then you get what is popular). You never choose the algorithm; +strategy+ in the response says what was used.
    # Send +recommendation_id+ back on the events that follow.
    #
    # @param user_id [String, nil]
    # @param session_id [String, nil]
    # @param item_id [String, nil] the item being looked at (e.g. the product page)
    # @param viewed_item_ids [Array<String>, nil] recently viewed items, oldest first
    # @param placement [String, nil] free-form label of where the recommendations will be shown
    # @param limit [Integer, nil] how many items to return (1-100, default 10)
    # @param debug [Boolean] diagnostic detail in +explanation+. Secret key only.
    # @return [RecommendationResponse]
    def get(user_id: nil, session_id: nil, item_id: nil, viewed_item_ids: nil, placement: nil, limit: nil, debug: false,
            timeout: nil, max_retries: nil)
      body = Support.compact(
        "user_id" => user_id.nil? ? nil : Support.require_id(user_id, "user_id"),
        "session_id" => session_id.nil? ? nil : Support.require_id(session_id, "session_id"),
        "item_id" => item_id.nil? ? nil : Support.require_id(item_id, "item_id"),
        "viewed_item_ids" => viewed_item_ids&.map { |id| Support.require_id(id, "viewed_item_ids[]") },
        "placement" => placement, "count" => limit, "debug" => debug ? true : nil
      )
      # A read: repeating it only mints another recommendation_id, so it is retried.
      Wire.recommendation(@http.request("POST", "/getRec", body: body, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    # ---- Advanced Recommendations --------------------------------------------------------------------------
    # One strategy at a time, for expert use. Ids travel in the URL path here, so they cannot contain "/".
    # All of them also take +placement:+, +session_id:+ (attaches the recommendation to an anonymous session, for
    # attribution) and +limit:+ (default 10).

    # The most popular items - the fallback for a visitor with no history at all.
    def popular(limit: nil, placement: nil, session_id: nil, timeout: nil, max_retries: nil)
      advanced("/getRec/popular/#{Support.limit_of(limit)}", placement, session_id, {}, timeout, max_retries)
    end

    # Items similar to one item (content similarity). No user needed.
    def similar(item_id:, limit: nil, placement: nil, session_id: nil, timeout: nil, max_retries: nil)
      item = Support.require_path_safe_id(item_id, "item_id")
      advanced("/getRec/content/#{Support.encode(item)}/#{Support.limit_of(limit)}", placement, session_id, {}, timeout, max_retries)
    end

    # Collaborative filtering: what users with similar histories liked. Needs a trained model.
    def collaborative(user_id:, limit: nil, placement: nil, session_id: nil, timeout: nil, max_retries: nil)
      user = Support.require_path_safe_id(user_id, "user_id")
      advanced("/getRec/collaborative/#{Support.encode(user)}/#{Support.limit_of(limit)}", placement, session_id, {}, timeout, max_retries)
    end

    # Similar items, personalized for a user. +alpha+ (0-1) weighs the collaborative signal against content
    # similarity: 0 = pure content, 1 = pure collaborative (the API's default is 0.5).
    def hybrid(user_id:, item_id:, alpha: nil, limit: nil, placement: nil, session_id: nil, timeout: nil, max_retries: nil)
      user = Support.require_path_safe_id(user_id, "user_id")
      item = Support.require_path_safe_id(item_id, "item_id")
      path = "/getRec/hybrid/#{Support.encode(user)}/#{Support.encode(item)}/#{Support.limit_of(limit)}"
      advanced(path, placement, session_id, { "alpha" => alpha }, timeout, max_retries)
    end

    # Recency-weighted recommendations from what was viewed: pass +viewed_item_ids+ (an explicit list, oldest
    # first) **or** +user_id+ (LIKYLY's own history of that user's views) - exactly one.
    def session(viewed_item_ids: nil, user_id: nil, limit: nil, placement: nil, session_id: nil, timeout: nil, max_retries: nil)
      if viewed_item_ids.nil? == user_id.nil?
        raise ValidationError, "session needs either viewed_item_ids or user_id (exactly one)"
      end

      count = Support.limit_of(limit)
      unless user_id.nil?
        user = Support.require_path_safe_id(user_id, "user_id")
        return advanced("/getRec/sessionForUser/#{Support.encode(user)}/#{count}", placement, session_id, {}, timeout, max_retries)
      end

      raise ValidationError, "viewed_item_ids must be a non-empty Array" unless viewed_item_ids.is_a?(Array) && !viewed_item_ids.empty?

      ids = viewed_item_ids.map { |id| Support.require_id(id, "viewed_item_ids[]") }
      if ids.any? { |id| id.include?(",") }
        raise ValidationError, 'an item id containing "," cannot be sent in this endpoint\'s comma-separated list - use recommendations.get'
      end

      advanced("/getRec/session", placement, session_id, { "viewed_item_ids" => ids.join(","), "count" => count }, timeout, max_retries)
    end

    private

    def advanced(path, placement, session_id, extra, timeout, max_retries)
      # response_format=object: always the same {recommendation_id, strategy, items} envelope
      query = { "response_format" => "object", "placement" => placement, "session_id" => session_id }.merge(extra)
      Wire.recommendation(@http.request("GET", path, query: query, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end
  end
end
