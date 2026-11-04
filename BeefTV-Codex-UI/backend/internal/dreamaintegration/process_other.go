//go:build !windows

package dreamaintegration

import "os/exec"

func hideProcess(command *exec.Cmd) {}
