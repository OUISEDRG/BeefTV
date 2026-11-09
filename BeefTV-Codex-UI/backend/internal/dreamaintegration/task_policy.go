package dreamaintegration

import "encoding/json"

// MatchesGenerationTask uses the saved routing identity, which remains readable
// when the credential fields in the input have been encrypted.
func MatchesGenerationTask(rawInput string) bool {
	var input struct {
		Config struct {
			ChannelID string `json:"channelId"`
			Protocol  string `json:"interfaceType"`
		} `json:"config"`
	}
	if json.Unmarshal([]byte(rawInput), &input) != nil {
		return false
	}
	switch input.Config.Protocol {
	case "dreama-videos-async", "dreama-images-async", "dreama-extension-async", "dreama-enhancement-async", "dreama-audio-async":
		return true
	case "seedance-videos-compatible":
		return input.Config.ChannelID == "dreama-video-enhancement"
	default:
		return false
	}
}
