package canvasgeneration

// TaskInput joins credential-free compiler output with the configured model
// connection. This is consumed only after authoritative recompilation and CAS.
func TaskInput(item Item, connection map[string]any) (map[string]any, error) {
	if item.Kind != "video" || item.Config["model"] == nil || item.Config["model"] != connection["model"] || item.Config["interfaceType"] != connection["interfaceType"] {
		return nil, failure("generation_config_changed", "编译型号与当前配置不一致，请重新准备")
	}
	config := map[string]any{}
	for k, v := range connection {
		config[k] = v
	}
	for k, v := range item.Config {
		switch k {
		case "interfaceType", "model", "size", "videoSeconds", "vquality", "videoGenerateAudio", "videoWatermark", "capabilityConfig":
			config[k] = v
		default:
			return nil, failure("generation_compiler_contract", "编译器返回了不支持的生成参数")
		}
	}
	metadata := map[string]any{}
	for k, v := range item.Metadata {
		metadata[k] = v
	}
	metadata["nodeId"], metadata["sourceNodeId"] = item.NodeID, item.NodeID
	metadata["outputNodeIds"], metadata["source"] = []string{item.NodeID}, "canvas-external"
	return map[string]any{"mode": item.Kind, "prompt": item.Prompt, "config": config, "metadata": metadata, "referenceImages": item.ReferenceImages, "referenceVideos": item.ReferenceVideos, "referenceAudios": item.ReferenceAudios}, nil
}
