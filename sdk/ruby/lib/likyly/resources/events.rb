# frozen_string_literal: true

module Likyly
  # +client.events+ - what your visitors do. Works with the **public** key from a browser, or the secret key from
  # your backend. +track+ is the one mechanism; +view+, +click+, ... are shortcuts that call it with the matching
  # event type.
  #
  # An event needs an +item_id+ and a +user_id+ or a +session_id+ (or both: that ties an anonymous session to the
  # user). Every event method also accepts +timeout:+ and +max_retries:+.
  class Events
    EVENT_TYPE = /\A[A-Za-z0-9_-]{1,64}\z/

    # @api private
    def initialize(http)
      @http = http
    end

    # Records one event of any type: "view", "click", ... or your own ("favorite", "share", ...). Event types are
    # open strings - the six helpers below are conveniences, not a closed list.
    #
    # @param type [String]
    # @param item_id [String]
    # @param user_id [String, nil]
    # @param session_id [String, nil] anonymous visitor / browsing session - no login needed
    # @param recommendation_id [String, nil] the +recommendation_id+ of the recommendation that surfaced this item - enables attribution
    # @param placement [String, nil] free-form label of where this happened: "homepage", "product_page", "cart", ...
    # @param quantity [Integer, nil]
    # @param occurred_at [Time, String, nil] defaults to the server's time
    # @param properties [Hash, nil] free-form; keys are yours
    # @param event_id [String, nil] idempotency key, unique per account: replaying an event with the same +event_id+
    #   records nothing and returns +duplicate: true+. Set it on purchases - it makes retries safe.
    # @return [EventResult]
    #
    # Retries: a failed call is only retried automatically when it carries an +event_id+ (then a replay is
    # harmless); without one, an ambiguous failure is raised rather than risking a duplicate.
    def track(type, item_id: nil, user_id: nil, session_id: nil, recommendation_id: nil, placement: nil,
              quantity: nil, occurred_at: nil, properties: nil, event_id: nil, timeout: nil, max_retries: nil)
      body = event_body(item_id: item_id, user_id: user_id, session_id: session_id, recommendation_id: recommendation_id,
                        placement: placement, quantity: quantity, occurred_at: occurred_at, properties: properties, event_id: event_id)
      response = @http.request("POST", "/events/#{Support.encode(require_type(type))}", body: body, idempotent: !event_id.nil?,
                                                                                            timeout: timeout, max_retries: max_retries).json
      EventResult.new(message: response["message"], event_id: response["event_id"], duplicate: response["duplicate"] == true)
    end

    # Up to 1000 events in one call, each a Hash with its own +type:+. All-or-nothing validation.
    # @param events [Array<Hash>] +{ type: "view", user_id:, item_id:, ... }+
    # @return [EventBatchResult]
    def track_many(events, timeout: nil, max_retries: nil)
      raise ValidationError, "events must be a non-empty Array" unless events.is_a?(Array) && !events.empty?

      entries = events.map do |e|
        raise ValidationError, "each event must be a Hash" unless e.is_a?(Hash)

        unknown = e.keys - BODY_KEYS - [:type]
        raise ValidationError, "unknown event field#{"s" if unknown.size > 1}: #{unknown.map(&:inspect).join(", ")}" unless unknown.empty?

        event_body(**BODY_KEYS.to_h { |k| [k, e[k]] }).merge("event_type" => require_type(e[:type]))
      end
      idempotent = events.all? { |e| !e[:event_id].nil? }
      r = @http.request("POST", "/events/batch", body: { "events" => entries }, idempotent: idempotent, timeout: timeout, max_retries: max_retries).json
      EventBatchResult.new(received: r["received"], accepted: r["accepted"], duplicates: r["duplicates"])
    end

    # The item was shown to the visitor (send the +recommendation_id+ it came with).
    def impression(**event) = track("impression", **event)

    # The visitor looked at the item (a product page, an article).
    def view(**event) = track("view", **event)

    # The visitor clicked the item.
    def click(**event) = track("click", **event)

    # The visitor added the item to their cart.
    def add_to_cart(**event) = track("add_to_cart", **event)

    # The visitor removed the item from their cart.
    def remove_from_cart(**event) = track("remove_from_cart", **event)

    # The visitor bought the item. Set +event_id+ (e.g. +"purchase_#{order_id}_#{item_id}"+) so a retry can never
    # count the purchase twice.
    def purchase(**event) = track("purchase", **event)

    BODY_KEYS = %i[item_id user_id session_id recommendation_id placement quantity occurred_at properties event_id].freeze
    private_constant :BODY_KEYS

    private

    def require_type(type)
      return type if type.is_a?(String) && type.match?(EVENT_TYPE)

      raise ValidationError, 'event type must be 1-64 characters of letters, digits, "_" or "-" (e.g. "view", "add_to_cart", "favorite")'
    end

    # +properties+ is passed through untouched: its keys are yours.
    def event_body(item_id:, user_id:, session_id:, recommendation_id:, placement:, quantity:, occurred_at:, properties:, event_id:)
      Support.require_id(item_id, "item_id")
      raise ValidationError, "an event needs a user_id or a session_id (or both)" if user_id.nil? && session_id.nil?

      Support.require_id(user_id, "user_id") unless user_id.nil?
      Support.require_id(session_id, "session_id") unless session_id.nil?
      Support.compact(
        "event_id" => event_id, "user_id" => user_id, "session_id" => session_id, "item_id" => item_id,
        "recommendation_id" => recommendation_id, "placement" => placement, "quantity" => quantity,
        "occurred_at" => occurred_at.is_a?(Time) ? occurred_at.getutc.iso8601(3) : occurred_at,
        "properties" => properties
      )
    end
  end
end
