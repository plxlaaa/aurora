//go:build windows

package tui

import (
	"os"
	"syscall"
	"unsafe"
)

func EnableColor() {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	get := kernel32.NewProc("GetConsoleMode")
	set := kernel32.NewProc("SetConsoleMode")
	h := syscall.Handle(os.Stdout.Fd())
	var mode uint32
	r, _, _ := get.Call(uintptr(h), uintptr(unsafe.Pointer(&mode)))
	if r == 0 {
		return
	}
	const enableVirtualTerminalProcessing = 0x0004
	_, _, _ = set.Call(uintptr(h), uintptr(mode|enableVirtualTerminalProcessing))
}
