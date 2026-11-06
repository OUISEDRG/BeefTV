package canvasgeneration

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"

	"infinite-canvas/backend/internal/assistantruntime"
)

const maximumCompilerOutput = 8 << 20

type limitedBuffer struct{ bytes.Buffer }

func (w *limitedBuffer) Write(p []byte) (int, error) {
	if w.Len()+len(p) > maximumCompilerOutput {
		return 0, errors.New("compiler output exceeds limit")
	}
	return w.Buffer.Write(p)
}

func RunBundled(ctx context.Context, request Request) (Compiled, error) {
	executable, err := os.Executable()
	if err != nil {
		return Compiled{}, failure("generation_compiler_unavailable", "无法定位随包画布编译器")
	}
	config := assistantruntime.BundledConfig(executable, runtime.GOOS)
	if config.HostCommand == "" || len(config.HostArgs) != 1 {
		return Compiled{}, failure("generation_compiler_unavailable", "随包画布编译器不可用，请更新完整专用程序包")
	}
	entry := filepath.Join(filepath.Dir(config.HostArgs[0]), "dreama-canvas-compiler.mjs")
	if info, err := os.Stat(entry); err != nil || info.IsDir() {
		return Compiled{}, failure("generation_compiler_unavailable", "随包画布编译器缺失，请更新完整专用程序包")
	}
	return runProcess(ctx, config.HostCommand, entry, request)
}

func runProcess(ctx context.Context, node, entry string, request Request) (Compiled, error) {
	encoded, err := json.Marshal(request)
	if err != nil {
		return Compiled{}, failure("generation_compiler_input", "无法读取当前画布或模型配置")
	}
	if len(encoded) > maximumCompilerOutput {
		return Compiled{}, failure("generation_compiler_input", "画布编译输入过大，请缩小当前画布")
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, node, entry)
	hideCompiler(command)
	command.Stdin = bytes.NewReader(encoded)
	var output limitedBuffer
	command.Stdout = &output
	command.Stderr = io.Discard // Never expose config, credentials or a JS stack.
	processErr := command.Run()
	if ctx.Err() != nil {
		return Compiled{}, failure("generation_compiler_timeout", "画布准备已中断或超时，没有提交生成")
	}
	var envelope struct {
		Compiled
		Error *Failure `json:"error"`
	}
	if json.Unmarshal(output.Bytes(), &envelope) != nil {
		return Compiled{}, failure("generation_compiler_failed", "画布编译器未返回有效结果，没有提交生成")
	}
	if envelope.Error != nil && envelope.Error.Reason != "" && envelope.Error.Message != "" {
		return Compiled{}, envelope.Error
	}
	if processErr != nil {
		return Compiled{}, failure("generation_compiler_failed", "画布编译失败，没有提交生成")
	}
	return envelope.Compiled, nil
}
