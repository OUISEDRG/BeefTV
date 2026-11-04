package modelcatalog

import (
	"encoding/json"
	"strings"
)

type PromptModelIdentity struct{ ChannelID, Model, Protocol, BaseURL string }

func ResolvePromptOptional(identity PromptModelIdentity, readSaved func() ([]byte, error), lookup ChannelModelLookup) (bool, error) {
	if identity.ChannelID != "" {
		if lookup == nil {
			return false, nil
		}
		item, err := lookup(identity.ChannelID, identity.Model)
		if err != nil {
			return false, err
		}
		if item == nil || !item.Enabled || item.Capability != "video" || string(item.Protocol) != identity.Protocol {
			return false, nil
		}
		capability, err := DecodeModelCapabilityConfig(item.CapabilityConfigJSON)
		if err != nil {
			return false, err
		}
		return declaredPromptOptional(capability), nil
	}
	if readSaved == nil || identity.Model == "" || identity.Protocol == "" || identity.BaseURL == "" {
		return false, nil
	}
	body, err := readSaved()
	if err != nil {
		return false, err
	}
	var saved struct {
		Channels []struct {
			Scope         string   `json:"scope"`
			Enabled       bool     `json:"enabled"`
			BaseURL       string   `json:"baseUrl"`
			Models        []string `json:"models"`
			ModelProfiles []struct {
				Model      string                 `json:"model"`
				Protocol   string                 `json:"protocol"`
				Capability string                 `json:"capability"`
				Version    string                 `json:"videoCapabilitiesVersion"`
				Config     *ModelCapabilityConfig `json:"capabilityConfig"`
			} `json:"modelProfiles"`
		} `json:"channels"`
	}
	if err := json.Unmarshal(body, &saved); err != nil {
		return false, err
	}
	matches, optional := 0, false
	for _, channel := range saved.Channels {
		if !channel.Enabled || channel.Scope != "user" || strings.TrimRight(channel.BaseURL, "/") != strings.TrimRight(identity.BaseURL, "/") || !containsCapabilityString(channel.Models, identity.Model) {
			continue
		}
		for _, profile := range channel.ModelProfiles {
			if profile.Model != identity.Model || profile.Protocol != identity.Protocol || profile.Capability != "video" {
				continue
			}
			matches++
			optional = strings.TrimSpace(profile.Version) != "" && declaredPromptOptional(profile.Config)
		}
	}
	return matches == 1 && optional, nil
}

func declaredPromptOptional(capability *ModelCapabilityConfig) bool {
	return capability != nil && capability.Video != nil && capability.Video.PromptRequired != nil && !*capability.Video.PromptRequired && validateVideoCapabilityConfig(capability.Video) == nil
}
