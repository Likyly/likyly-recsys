# frozen_string_literal: true

# Official Ruby client for the LIKYLY recommendations API.
#
#   client = Likyly::Client.new(api_key: ENV.fetch("LIKYLY_SECRET_KEY"))
#
#   # Track what a visitor does
#   client.events.view(user_id: "user_123", item_id: "SKU-456")
#
#   # Ask what to show them
#   recs = client.recommendations.get(user_id: "user_123", placement: "homepage", limit: 8)
#   recs.items.each { |item| puts item.item_id }
#
# There are two kinds of API key. The **secret** key is for your backend only (items, users, everything). The
# **public** key is safe to expose in a web page but can only ask for recommendations and record events. Never
# ship the secret key in code that runs on a visitor's device.
module Likyly
end

require_relative "likyly/version"
require_relative "likyly/errors"
require_relative "likyly/support"
require_relative "likyly/models"
require_relative "likyly/http"
require_relative "likyly/resources/items"
require_relative "likyly/resources/users"
require_relative "likyly/resources/events"
require_relative "likyly/resources/recommendations"
require_relative "likyly/client"
