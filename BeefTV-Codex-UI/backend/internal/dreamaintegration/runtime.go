package dreamaintegration

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"

	"infinite-canvas/backend/internal/outbound"
)

// RuntimeConfig is written by the installer in the active workspace data root.
// No executable, script or working directory is accepted from an HTTP request.
type RuntimeConfig struct {
	SchemaVersion    int    `json:"schemaVersion"`
	PythonExecutable string `json:"pythonExecutable"`
	BridgeDirectory  string `json:"bridgeDirectory"`
	Origin           string `json:"origin"`
}

var startupMu sync.Mutex

func (s *Service) Ensure(ctx context.Context, channelID, dataDir string) (json.RawMessage, error) {
	startupMu.Lock()
	defer startupMu.Unlock()
	status, err := s.Exchange(ctx, channelID, "GET", "status", nil, nil)
	if err == nil {
		return status, nil
	}
	var public *Error
	if !errors.As(err, &public) || public.Reason != "dreama_bridge_unavailable" {
		return nil, err
	}
	_, endpoint, err := s.resolve(channelID)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(filepath.Join(dataDir, "dreama-integration.json"))
	if err != nil || len(raw) > 16384 {
		return nil, problem(503, "dreama_runtime_missing", "剧梦运行配置尚未安装，请运行专用版安装器")
	}
	var config RuntimeConfig
	if json.Unmarshal(raw, &config) != nil || config.SchemaVersion != 1 || !filepath.IsAbs(config.PythonExecutable) || !filepath.IsAbs(config.BridgeDirectory) {
		return nil, problem(503, "dreama_runtime_invalid", "剧梦运行配置无效")
	}
	origin, e := outbound.ValidateLoopbackURL(config.Origin)
	if e != nil || origin.Scheme != endpoint.Scheme || origin.Host != endpoint.Host || (origin.Path != "" && origin.Path != "/") {
		return nil, problem(409, "dreama_runtime_mismatch", "剧梦运行配置与所选渠道不一致")
	}
	for _, path := range []string{config.PythonExecutable, filepath.Join(config.BridgeDirectory, "server.py")} {
		info, e := os.Stat(path)
		if e != nil || !info.Mode().IsRegular() {
			return nil, problem(503, "dreama_runtime_missing", "剧梦运行文件缺失，请修复专用版安装")
		}
	}
	launch := s.launch
	if launch == nil {
		launch = startRuntime
	}
	if err = launch(config); err != nil {
		return nil, problem(503, "dreama_start_failed", "剧梦桥接服务启动失败，请检查本机运行环境")
	}
	for attempt := 0; attempt < 40; attempt++ {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
		status, err = s.Exchange(ctx, channelID, "GET", "status", nil, nil)
		if err == nil {
			return status, nil
		}
		if !errors.As(err, &public) || public.Reason != "dreama_bridge_unavailable" {
			return nil, err
		}
	}
	return nil, problem(503, "dreama_start_timeout", "剧梦桥接仍未就绪，请检查本机运行日志")
}
func startRuntime(config RuntimeConfig) error {
	command := exec.Command(config.PythonExecutable, "-B", filepath.Join(config.BridgeDirectory, "server.py"))
	command.Dir = config.BridgeDirectory
	hideProcess(command)
	logDir := filepath.Join(config.BridgeDirectory, "state")
	if err := os.MkdirAll(logDir, 0700); err != nil {
		return err
	}
	log, err := os.OpenFile(filepath.Join(logDir, "workbench-runtime.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	command.Stdout = log
	command.Stderr = log
	if err := command.Start(); err != nil {
		return err
	}
	return command.Process.Release()
}
