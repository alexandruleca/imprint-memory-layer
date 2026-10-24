package gpustate

import "testing"

func TestSameEnvComparesRuntimeExactly(t *testing.T) {
	host := Env{GPU: "RTX", Driver: "581.80", Python: "3.12"}
	measured := host
	measured.Runtime = "a"
	cases := []struct {
		name     string
		recorded Env
		current  Env
		want     bool
	}{
		{"same runtime", measured, measured, true},
		{"other runtime", measured, Env{GPU: "RTX", Driver: "581.80", Python: "3.12", Runtime: "b"}, false},
		{"recorded before runtimes", host, measured, false},
		{"llama verdicts never set it", host, host, true},
		{"other fields stay wildcards", Env{GPU: "RTX", Runtime: "a"}, measured, true},
	}
	for _, c := range cases {
		if got := SameEnv(c.recorded, c.current); got != c.want {
			t.Errorf("%s: SameEnv = %v, want %v", c.name, got, c.want)
		}
	}
}
