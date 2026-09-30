# frozen_string_literal: true

module Likyly
  # +client.items+ - your catalog. Needs the **secret** API key.
  #
  # Every method also accepts +timeout:+ (seconds) and +max_retries:+ to override the client's for that call.
  class Items
    # @api private
    def initialize(http)
      @http = http
    end

    # One item by your own id.
    # @return [Item]
    def get(item_id, timeout: nil, max_retries: nil)
      id = Support.require_id(item_id, "item_id")
      Wire.item(@http.request("GET", "/items/#{Support.encode(id)}", idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    # One page of the catalog. Pagination is +limit+ + +offset+ (the API's default is 100 per page); +total+ is
    # the whole catalog's size.
    # @return [ItemList]
    def list(limit: nil, offset: nil, timeout: nil, max_retries: nil)
      response = @http.request("GET", "/items", query: { "limit" => limit, "offset" => offset }, idempotent: true, timeout: timeout, max_retries: max_retries)
      ItemList.new(items: response.json.map { |w| Wire.item(w) }, total: total_of(response), limit: limit, offset: offset || 0)
    end

    # Creates the item, or replaces it if it exists - idempotent, safe to call as often as you like. The body is
    # the whole item: fields you leave out are cleared.
    # @param properties [Hash, nil] +category+ and +description+ feed content similarity; everything else is stored as is
    # @return [Item]
    def upsert(item_id, title: nil, description: nil, properties: nil, timeout: nil, max_retries: nil)
      id = Support.require_id(item_id, "item_id")
      body = item_body(title, description, properties)
      Wire.item(@http.request("PUT", "/items/#{Support.encode(id)}", body: body, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    # Removes the item from the catalog. Events already recorded for it are kept.
    # @return [nil]
    def delete(item_id, timeout: nil, max_retries: nil)
      id = Support.require_id(item_id, "item_id")
      @http.request("DELETE", "/items/#{Support.encode(id)}", idempotent: true, timeout: timeout, max_retries: max_retries)
      nil
    end

    # Batch upsert (1-1000 items) - each entry behaves like +upsert+. A failing entry is reported in +errors+.
    # @param items [Array<Hash>] +{ item_id:, title:, description:, properties: }+
    # @return [BatchResult]
    def upsert_many(items, timeout: nil, max_retries: nil)
      raise ValidationError, "items must be a non-empty Array" unless items.is_a?(Array) && !items.empty?

      entries = items.map do |i|
        raise ValidationError, "each item must be a Hash" unless i.is_a?(Hash)

        item_body(i[:title], i[:description], i[:properties]).merge("item_id" => Support.require_id(i[:item_id], "item_id"))
      end
      # An upsert: replaying it changes nothing, so it is retried.
      Wire.batch(@http.request("POST", "/items/import", body: { "items" => entries }, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    # Alias of {#upsert_many}: the API's +POST /items/import+ is a JSON batch upsert. (A CSV import exists only in
    # the LIKYLY dashboard.)
    def import(items, **options)
      upsert_many(items, **options)
    end

    # Batch delete (1-1000 ids). Ids that don't exist are reported in +errors+.
    # @return [BatchResult]
    def delete_many(item_ids, timeout: nil, max_retries: nil)
      raise ValidationError, "item_ids must be a non-empty Array" unless item_ids.is_a?(Array) && !item_ids.empty?

      ids = item_ids.map { |id| Support.require_id(id, "item_id") }
      Wire.batch(@http.request("POST", "/items/delete", body: { "item_ids" => ids }, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    private

    def item_body(title, description, properties)
      raise ValidationError, "an item needs a non-empty title" unless title.is_a?(String) && !title.empty?

      Support.compact("title" => title, "description" => description, "properties" => properties)
    end

    def total_of(response)
      value = response.headers["x-total-count"]
      value && Integer(value, exception: false)
    end
  end
end
