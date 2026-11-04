# frozen_string_literal: true

module Likyly
  # +client.placements+ - the recommended way to ask for recommendations. A placement ("what to recommend, where,
  # with what fallback and filters") is configured once, in LIKYLY (dashboard or MCP admin tools) - never from this
  # SDK - and called by its slug from here on. +client.recommendations+ remains available for per-call use with no
  # stored configuration.
  #
  # Works with the **public** key from a browser, or the secret key from your backend. Every method also accepts
  # +timeout:+ and +max_retries:+ to override the client's for that call.
  class Placements
    # @api private
    def initialize(http)
      @http = http
    end

    # Recommendations for one placement. Send whatever context you have - the placement's own configuration
    # decides the strategy, fallback and filters, not you. Send +recommendation_id+ back on the events that follow.
    #
    #   client.placements.recommend(placement: "pdp-related", context: { item_id: "SKU-456", user_id: "user_123" })
    #
    # @param placement [String] the placement's slug, e.g. "pdp-related"
    # @param context [Hash, nil] what you know about this page view, all optional:
    #   +item_id+ (the item being looked at), +category_id+, +collection_id+, +user_id+, +anonymous_id+ (stable
    #   anonymous visitor id, used like +session_id+ when that is omitted), +session_id+, +cart_item_ids+ (Array),
    #   +locale+, +custom_context+ (Hash, passed through untouched: its keys are yours)
    # @param limit [Integer, nil] overrides the placement's own configured limit for this call
    # @return [PlacementRecommendation]
    def recommend(placement:, context: nil, limit: nil, timeout: nil, max_retries: nil)
      slug = Support.require_id(placement, "placement")
      unless limit.nil? || (limit.is_a?(Integer) && limit >= 1)
        raise ValidationError, "limit must be a positive Integer"
      end

      body = Support.compact("context" => context_body(context), "limit" => limit)
      # Each call mints a fresh recommendation_id: repeating it on a dropped connection is harmless, so it is retried.
      response = @http.request("POST", "/placements/#{Support.encode(slug)}/recommend", body: body, idempotent: true,
                                                                                         timeout: timeout, max_retries: max_retries)
      Wire.placement_recommendation(response.json)
    end

    # Context key => wire name.
    CONTEXT_KEYS = {
      item_id: "current_item_id", category_id: "category_id", collection_id: "collection_id", user_id: "user_id",
      anonymous_id: "anonymous_id", session_id: "session_id", cart_item_ids: "cart_item_ids", locale: "locale",
      custom_context: "custom_context"
    }.freeze
    private_constant :CONTEXT_KEYS

    private

    def context_body(context)
      return nil if context.nil?
      raise ValidationError, "context must be a Hash" unless context.is_a?(Hash)

      unknown = context.keys - CONTEXT_KEYS.keys
      raise ValidationError, "unknown context field#{"s" if unknown.size > 1}: #{unknown.map(&:inspect).join(", ")}" unless unknown.empty?

      Support.compact(CONTEXT_KEYS.to_h { |key, wire| [wire, context[key]] })
    end
  end
end
