//go:build windows

package canvasgeneration

import (
	"os/exec"
	"syscall"
)

func hideCompiler(command *exec.Cmd) { command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true} }
