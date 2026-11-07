# frozen_string_literal: true

require_relative "lib/likyly/version"

Gem::Specification.new do |spec|
  spec.name = "likyly"
  spec.version = Likyly::VERSION
  spec.authors = ["LIKYLY"]
  spec.summary = "Product recommendations for your site, learned from what your visitors do. Official LIKYLY client."
  spec.description = "LIKYLY shows each visitor the products they are most likely to want (\"You may also like\", \"Recommended for you\"). Send your catalog and what visitors do, ask what to show. No runtime dependencies, no Rails required."
  spec.homepage = "https://www.likyly.com"
  spec.license = "MIT"
  spec.required_ruby_version = ">= 3.0"
  spec.metadata = {
    "homepage_uri" => spec.homepage,
    "documentation_uri" => "https://likyly.com/docs/sdk/ruby",
    "source_code_uri" => "https://github.com/Likyly/likyly-recsys/tree/main/sdk/ruby",
    "bug_tracker_uri" => "https://github.com/Likyly/likyly-recsys/issues",
    "rubygems_mfa_required" => "true"
  }
  spec.files = Dir["lib/**/*.rb"] + ["README.md", "LICENSE"].select { |f| File.exist?(f) }
  spec.require_paths = ["lib"]
end
