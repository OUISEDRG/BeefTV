package bootstrap

import "golang.org/x/sys/unix"

func publishFileWithoutReplacing(from, to string) error {
	return unix.RenamexNp(from, to, unix.RENAME_EXCL)
}
