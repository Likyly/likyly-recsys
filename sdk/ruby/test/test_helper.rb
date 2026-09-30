# frozen_string_literal: true

require "json"
require "minitest/autorun"
require "net/http"
require "uri"

$LOAD_PATH.unshift File.expand_path("../lib", __dir__)
require "likyly"

# Scripts one HTTP outcome per call; the last step repeats once the script is exhausted. A step is a Hash:
# status:, headers:, body: (marshalled to JSON), raw:, fail: (an exception the transport raises).
class FakeTransport
  Call = Struct.new(:method, :url, :headers, :body, :timeout) do
    def uri = URI.parse(url)
    def path = url.sub(%r{\Ahttps?://[^/]+}, "").split("?", 2).first
    def query = URI.decode_www_form(uri.query.to_s).to_h
    def json = JSON.parse(body)
  end

  attr_reader :calls

  def initialize(steps)
    @steps = steps
    @calls = []
  end

  def call(method, url, headers, body, timeout)
    @calls << Call.new(method, url, headers, body, timeout)
    step = @steps[[@calls.size, @steps.size].min - 1]
    raise step[:fail] if step[:fail]

    payload = step[:raw] || (step[:body].nil? ? "" : JSON.generate(step[:body]))
    [step[:status] || 200, (step[:headers] || {}).transform_keys(&:downcase), payload]
  end
end

Harness = Struct.new(:client, :transport, :sleeps) do
  def calls = transport.calls
end

module Helpers
  DEFAULTS = { api_key: "sk_test_conformance", base_url: "https://api.example.test" }.freeze

  # A client over a scripted transport: no real waiting, jitter pinned to 1 (so a backoff is exactly
  # base * 2**attempt).
  def harness(steps, **options)
    transport = FakeTransport.new(steps)
    sleeps = []
    client = Likyly::Client.new(**DEFAULTS, **options, transport: transport, sleeper: ->(s) { sleeps << s }, random: -> { 1.0 })
    Harness.new(client, transport, sleeps)
  end

  REC = { "recommendation_id" => "rec_1", "strategy" => "popular", "items" => [] }.freeze
  EVT = { "message" => "ok", "event_id" => nil, "duplicate" => false }.freeze
  ITEM = { "item_id" => "a", "title" => "t" }.freeze
  DOWN = { status: 503, body: { "detail" => "down" } }.freeze
end
