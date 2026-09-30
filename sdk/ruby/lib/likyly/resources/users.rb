# frozen_string_literal: true

module Likyly
  # +client.users+ - optional user profiles. Needs the **secret** API key (profiles are personal data). You don't
  # have to create a user before sending events for them.
  class Users
    # @api private
    def initialize(http)
      @http = http
    end

    # @return [User]
    def get(user_id, timeout: nil, max_retries: nil)
      id = Support.require_id(user_id, "user_id")
      Wire.user(@http.request("GET", "/users/#{Support.encode(id)}", idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    # One page of users. Pagination is +limit+ + +offset+; without +limit+ the API returns every user.
    # @return [UserList]
    def list(limit: nil, offset: nil, timeout: nil, max_retries: nil)
      response = @http.request("GET", "/users", query: { "limit" => limit, "offset" => offset }, idempotent: true, timeout: timeout, max_retries: max_retries)
      total = response.headers["x-total-count"]
      UserList.new(users: response.json.map { |w| Wire.user(w) }, total: total && Integer(total, exception: false), limit: limit, offset: offset || 0)
    end

    # Creates or replaces the profile (idempotent). +properties+ is free-form: country, segment, language, ...
    # @return [User]
    def upsert(user_id, properties: nil, timeout: nil, max_retries: nil)
      id = Support.require_id(user_id, "user_id")
      body = Support.compact("properties" => properties)
      Wire.user(@http.request("PUT", "/users/#{Support.encode(id)}", body: body, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end

    # Erases the user: the profile **and every event recorded for them**.
    # @return [nil]
    def delete(user_id, timeout: nil, max_retries: nil)
      id = Support.require_id(user_id, "user_id")
      @http.request("DELETE", "/users/#{Support.encode(id)}", idempotent: true, timeout: timeout, max_retries: max_retries)
      nil
    end

    # Batch upsert (1-1000 users). A failing entry is reported in +errors+.
    # @param users [Array<Hash>] +{ user_id:, properties: }+
    # @return [BatchResult]
    def import(users, timeout: nil, max_retries: nil)
      raise ValidationError, "users must be a non-empty Array" unless users.is_a?(Array) && !users.empty?

      entries = users.map do |u|
        raise ValidationError, "each user must be a Hash" unless u.is_a?(Hash)

        Support.compact("user_id" => Support.require_id(u[:user_id], "user_id"), "properties" => u[:properties])
      end
      Wire.batch(@http.request("POST", "/users/import", body: { "users" => entries }, idempotent: true, timeout: timeout, max_retries: max_retries).json)
    end
  end
end
