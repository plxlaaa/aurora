//go:build windows

package tui

import (
	"syscall"
	"unsafe"
)

type coord struct{ x, y int16 }

type smallRect struct{ left, top, right, bottom int16 }

type consoleScreenBufferInfo struct {
	dwSize              coord
	dwCursorPosition    coord
	wAttributes         uint16
	srWindow            smallRect
	dwMaximumWindowSize coord
}

func termWidth() int {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	getStdHandle := kernel32.NewProc("GetStdHandle")
	getInfo := kernel32.NewProc("GetConsoleScreenBufferInfo")
	const stdOutputHandle = ^uintptr(10) // (DWORD)-11
	h, _, _ := getStdHandle.Call(stdOutputHandle)
	var info consoleScreenBufferInfo
	r, _, _ := getInfo.Call(h, uintptr(unsafe.Pointer(&info)))
	if r == 0 || info.srWindow.right < info.srWindow.left {
		return 80
	}
	return int(info.srWindow.right-info.srWindow.left) + 1
}
