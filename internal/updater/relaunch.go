//go:build !windows

package updater

func relaunchFromNew(exe, newBin string) error {
	_ = exe
	_ = newBin
	return nil
}
