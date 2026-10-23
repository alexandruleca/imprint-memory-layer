package cmd

import (
	"syscall"
	"unsafe"
)

var (
	procQueryFullProcessImageName = syscall.NewLazyDLL("kernel32.dll").NewProc("QueryFullProcessImageNameW")
	procNtQueryInformationProcess = syscall.NewLazyDLL("ntdll.dll").NewProc("NtQueryInformationProcess")
)

const (
	processQueryLimitedInformation = 0x1000
	// ProcessCommandLineInformation (Windows 8.1+) returns a UNICODE_STRING.
	processCommandLineInformation = 60
)

func listProcesses() ([]processInfo, error) {
	snapshot, err := syscall.CreateToolhelp32Snapshot(syscall.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil, err
	}
	defer syscall.CloseHandle(snapshot)
	var entry syscall.ProcessEntry32
	entry.Size = uint32(unsafe.Sizeof(entry))
	var procs []processInfo
	for err = syscall.Process32First(snapshot, &entry); err == nil; err = syscall.Process32Next(snapshot, &entry) {
		handle, err := syscall.OpenProcess(processQueryLimitedInformation, false, entry.ProcessID)
		if err != nil {
			continue // Elevated or another user's process.
		}
		exe, cmdline := imagePath(handle), commandLine(handle)
		syscall.CloseHandle(handle)
		if exe != "" || cmdline != "" {
			procs = append(procs, processInfo{PID: int(entry.ProcessID), Exe: exe, Cmdline: cmdline})
		}
	}
	return procs, nil
}

func imagePath(handle syscall.Handle) string {
	buf := make([]uint16, 32768)
	size := uint32(len(buf))
	ok, _, _ := procQueryFullProcessImageName.Call(uintptr(handle), 0, uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&size)))
	if ok == 0 {
		return ""
	}
	return syscall.UTF16ToString(buf[:size])
}

func commandLine(handle syscall.Handle) string {
	var size uint32
	procNtQueryInformationProcess.Call(uintptr(handle), processCommandLineInformation, 0, 0, uintptr(unsafe.Pointer(&size)))
	if size < uint32(unsafe.Sizeof(unicodeString{})) {
		return ""
	}
	buf := make([]byte, size)
	status, _, _ := procNtQueryInformationProcess.Call(uintptr(handle), processCommandLineInformation, uintptr(unsafe.Pointer(&buf[0])), uintptr(size), uintptr(unsafe.Pointer(&size)))
	if status != 0 {
		return ""
	}
	// The UNICODE_STRING's Buffer points into buf, after the header.
	header := (*unicodeString)(unsafe.Pointer(&buf[0]))
	if header.Buffer == nil || header.Length == 0 {
		return ""
	}
	return syscall.UTF16ToString(unsafe.Slice(header.Buffer, header.Length/2))
}

type unicodeString struct {
	Length        uint16
	MaximumLength uint16
	Buffer        *uint16
}
