package modelcatalog

import (
	"encoding/json"
	"infinite-canvas/backend/internal/kernel"
	"reflect"
	"strings"
)

func videoCapabilityForResolution(profile *VideoCapabilityConfig, resolution string) (*VideoCapabilityConfig, error) {
	variant, exists := profile.ResolutionVariants[resolution]
	if !exists {
		return profile, nil
	}
	result := *profile
	raw, err := json.Marshal(profile.References)
	if err != nil {
		return nil, err
	}
	var references map[string]json.RawMessage
	if err = json.Unmarshal(raw, &references); err != nil {
		return nil, err
	}
	knownFields := make(map[string]bool)
	referenceType := reflect.TypeOf(VideoReferenceConfig{})
	for index := 0; index < referenceType.NumField(); index++ {
		knownFields[strings.Split(referenceType.Field(index).Tag.Get("json"), ",")[0]] = true
	}
	for key, value := range variant.References {
		if !knownFields[key] {
			return nil, kernel.BadAuthRequest("视频变体引用限制字段无效")
		}
		references[key] = value
	}
	raw, err = json.Marshal(references)
	if err != nil {
		return nil, err
	}
	if err = json.Unmarshal(raw, &result.References); err != nil {
		return nil, kernel.BadAuthRequest("视频变体引用限制无效")
	}
	result.Duration = variant.Duration
	return &result, nil
}
