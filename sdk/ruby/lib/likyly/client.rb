# frozen_string_literal: true

module Likyly
  # The LIKYLY client. Four things to know:
  #
  #   client.items            your catalog
  #   client.users            your users (optional)
  #   client.events           what visitors do
  #   client.recommendations  what to show them
  #
  # A client holds no connection state: it is safe to share between threads.
  class Client
    attr_reader :items, :users, :events, :recommendations

    # @param api_key [String] Two kinds: the **secret** key (backend only: items, users, everything) and the
    #   **public** key (safe in a web page: recommendations and event tracking only). Never put the secret key
    #   in code that ships to a browser.
    # @param base_url [String] defaults to https://api.likyly.com
    # @param catalog [String, nil] which of your catalogs to use, if your account has several. Omit it if you have
    #   one: LIKYLY uses your only catalog.
    # @param timeout [Numeric] per-request timeout in seconds (applied to connecting, writing and each read). Default 10.
    # @param max_retries [Integer] automatic retries on transient failures (429, 502, 503, 504, dropped
    #   connections). Default 2. Use 0 to disable.
    # @param user_agent [String, nil] appended to the SDK's User-Agent, e.g. "my-shop/1.4"
    # @param transport [#call, nil] replaces Net::HTTP: +call(method, url, headers, body, timeout)+ returning
    #   +[status, headers, body]+ with lower-cased header names
    def initialize(api_key:, base_url: DEFAULT_BASE_URL, catalog: nil, timeout: 10, max_retries: 2, user_agent: nil,
                   transport: nil, sleeper: nil, random: nil)
      unless api_key.is_a?(String) && !api_key.strip.empty?
        raise ValidationError, "api_key is required (create one in your LIKYLY account)"
      end

      agent = "likyly-ruby/#{VERSION} ruby/#{RUBY_VERSION}"
      agent = "#{agent} #{user_agent}" if user_agent
      http = HttpCore.new(
        api_key: api_key, base_url: base_url, catalog: catalog, timeout: timeout, max_retries: max_retries,
        user_agent: agent, transport: transport || NetHttpTransport.new,
        sleeper: sleeper || ->(seconds) { sleep(seconds) }, random: random || -> { rand }
      )
      @items = Items.new(http)
      @users = Users.new(http)
      @events = Events.new(http)
      @recommendations = Recommendations.new(http)
    end
  end
end
