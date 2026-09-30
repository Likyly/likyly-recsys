package likyly

import "runtime"

// Version is the SDK version, sent in the User-Agent header.
const Version = "1.0.0"

// DefaultBaseURL is the production API.
const DefaultBaseURL = "https://api.likyly.com"

var userAgent = "likyly-go/" + Version + " (" + runtime.Version() + ")"
