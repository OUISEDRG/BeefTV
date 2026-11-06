package main

import (
	"encoding/json"
	"fmt"
	"io"
)

const maximumJSONParams = 8 << 20

// Only an explicit dash consumes stdin. This keeps ordinary CLI invocations
// nonblocking and avoids Windows command-line limits for saved canvas content.
func readJSONParams(value string, input io.Reader) (json.RawMessage, error) {
	data := []byte(value)
	if value == "-" {
		var err error
		data, err = io.ReadAll(io.LimitReader(input, maximumJSONParams+1))
		if err != nil {
			return nil, fmt.Errorf("unable to read JSON params from stdin")
		}
		if len(data) > maximumJSONParams {
			return nil, fmt.Errorf("JSON params from stdin exceed the 8 MiB limit")
		}
	}
	if !json.Valid(data) {
		return nil, fmt.Errorf("invalid JSON params")
	}
	return json.RawMessage(data), nil
}
