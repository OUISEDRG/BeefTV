//go:build !windows

package canvasgeneration

import "os/exec"

func hideCompiler(command *exec.Cmd) {}
