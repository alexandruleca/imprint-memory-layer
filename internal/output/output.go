package output

import (
	"fmt"
	"os"
)

var useColor = os.Getenv("NO_COLOR") == ""

const (
	red    = "\033[0;31m"
	green  = "\033[0;32m"
	yellow = "\033[1;33m"
	cyan   = "\033[0;36m"
	reset  = "\033[0m"
)

func color(c, msg string) string {
	if !useColor {
		return msg
	}
	return c + msg + reset
}

func Info(msg string) {
	fmt.Printf("%s %s\n", color(cyan, "[*]"), msg)
}

func Success(msg string) {
	fmt.Printf("%s %s\n", color(green, "[+]"), msg)
}

func Skip(msg string) {
	fmt.Printf("%s %s (already done)\n", color(green, "[-]"), msg)
}

func Warn(msg string) {
	fmt.Printf("%s %s\n", color(yellow, "[!]"), msg)
}

func Fail(msg string) {
	fmt.Printf("%s %s\n", color(red, "[x]"), msg)
	os.Exit(1)
}

func Header(msg string) {
	fmt.Printf("\n%s\n", color(green, msg))
}

func Line(msg string) {
	fmt.Println(msg)
}
