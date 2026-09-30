# frozen_string_literal: true

require_relative "lib/likyly/version"

Gem::Specification.new do |spec|
  spec.name = "likyly"
  spec.version = Likyly::VERSION
  spec.authors = ["LIKYLY"]
  spec.summary = "Official Ruby client for the LIKYLY recommendations API"
  spec.description = "Send your catalog and what visitors do, get recommendations back. No runtime dependencies, no Rails required."
  spec.homepage = "https://likyly.com"
  spec.license = "MIT"
  spec.required_ruby_version = ">= 3.0"
  spec.metadata = {
    "homepage_uri" => spec.homepage,
    "documentation_uri" => "https://likyly.com/docs/sdk/ruby",
    "rubygems_mfa_required" => "true"
  }
  spec.files = Dir["lib/**/*.rb"] + ["README.md", "LICENSE"].select { |f| File.exist?(f) }
  spec.require_paths = ["lib"]
end
