//go:build windows

package updater

import (
	"os"
	"os/exec"
)

func relaunchFromNew(exe, newBin string) error {
	cmd := exec.Command(newBin, os.Args[1:]...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Dir, _ = os.Getwd()
	cmd.Env = append(os.Environ(), "AURORA_REPLACE_TARGET="+exe)
	if err := cmd.Start(); err != nil {
		return err
	}
	os.Exit(0)
	return nil
}
