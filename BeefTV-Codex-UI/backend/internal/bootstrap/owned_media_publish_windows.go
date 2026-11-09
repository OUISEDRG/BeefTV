package bootstrap

import (
	"strings"

	"golang.org/x/sys/windows"
)

func publishFileWithoutReplacing(from, to string) error {
	source, err := windows.UTF16PtrFromString(extendedWindowsSavePath(from))
	if err != nil {
		return err
	}
	dest, err := windows.UTF16PtrFromString(extendedWindowsSavePath(to))
	if err != nil {
		return err
	}
	return windows.MoveFile(source, dest)
}

func extendedWindowsSavePath(path string) string {
	if strings.HasPrefix(path, `\\?\`) {
		return path
	}
	if strings.HasPrefix(path, `\\`) {
		return `\\?\UNC\` + path[2:]
	}
	return `\\?\` + path
}
