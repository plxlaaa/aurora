//go:build windows

package updater

import (
	"io"
	"os"
	"time"
)

func FinalizeReplacement() {
	target := os.Getenv("AURORA_REPLACE_TARGET")
	if target == "" {
		return
	}
	self, err := os.Executable()
	if err != nil {
		return
	}
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		_ = os.Remove(OldPath(target))
		if err := os.Rename(target, OldPath(target)); err != nil {
			time.Sleep(200 * time.Millisecond)
			continue
		}
		if err := copyFile(self, target); err != nil {
			_ = os.Rename(OldPath(target), target)
			time.Sleep(200 * time.Millisecond)
			continue
		}
		_ = os.Unsetenv("AURORA_REPLACE_TARGET")
		return
	}
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}
