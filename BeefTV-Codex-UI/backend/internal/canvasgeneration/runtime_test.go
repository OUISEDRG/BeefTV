package canvasgeneration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
)

// A process fixture exercises the stdin/stdout protocol without Node, network
// access, a model, or any media-generation implementation.
func TestCompilerSubprocess(t *testing.T) {
	mode := os.Getenv("BEEFTV_COMPILER_PROCESS_TEST")
	if mode == "" {
		return
	}
	var input Request
	if json.NewDecoder(os.Stdin).Decode(&input) != nil || input.SchemaVersion != 1 || len(input.NodeIDs) != 1 {
		os.Exit(2)
	}
	switch mode {
	case "ok":
		_ = json.NewEncoder(os.Stdout).Encode(fixtureOutput())
		os.Exit(0)
	case "failure":
		_, _ = fmt.Fprint(os.Stdout, `{"error":{"reason":"missing_binding","message":"请选择剧集"}}`)
		os.Exit(1)
	case "invalid":
		_, _ = fmt.Fprint(os.Stdout, "PRIVATE CONFIG")
		_, _ = fmt.Fprint(os.Stderr, "PRIVATE STACK")
		os.Exit(1)
	}
	os.Exit(2)
}

func TestCompilerProcessProtocol(t *testing.T) {
	for _, mode := range []string{"ok", "failure", "invalid"} {
		t.Run(mode, func(t *testing.T) {
			t.Setenv("BEEFTV_COMPILER_PROCESS_TEST", mode)
			compiled, err := runProcess(context.Background(), os.Args[0], "-test.run=^TestCompilerSubprocess$", fixtureRequest())
			if mode == "ok" {
				if err != nil || len(compiled.Items) != 1 || compiled.Items[0].NodeID != "shot" {
					t.Fatalf("invalid process result: %+v %v", compiled, err)
				}
				return
			}
			var failure *Failure
			if !errors.As(err, &failure) || strings.Contains(err.Error(), "PRIVATE") {
				t.Fatalf("unexpected process error: %v", err)
			}
			if mode == "failure" && failure.Reason != "missing_binding" {
				t.Fatalf("lost actionable error: %v", err)
			}
		})
	}
}

func TestCompilerCancellationAndLimits(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := runProcess(ctx, os.Args[0], "-test.run=^TestCompilerSubprocess$", fixtureRequest())
	var failure *Failure
	if !errors.As(err, &failure) || failure.Reason != "generation_compiler_timeout" {
		t.Fatalf("cancel not preserved: %v", err)
	}
	var output limitedBuffer
	if _, err := output.Write(make([]byte, maximumCompilerOutput+1)); err == nil || output.Len() != 0 {
		t.Fatal("accepted oversized output")
	}
	request := fixtureRequest()
	request.Config = json.RawMessage(`{"value":"` + strings.Repeat("x", maximumCompilerOutput) + `"}`)
	_, err = runProcess(context.Background(), "must-not-run", "entry", request)
	if !errors.As(err, &failure) || failure.Reason != "generation_compiler_input" {
		t.Fatalf("oversized input reached process: %v", err)
	}
}
