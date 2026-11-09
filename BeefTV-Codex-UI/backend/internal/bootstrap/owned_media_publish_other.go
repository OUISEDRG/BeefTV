//go:build !windows && !darwin

package bootstrap

import "os"

func publishFileWithoutReplacing(from, to string) error {
	return os.Link(from, to)
}
