package outbound

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestValidateLoopbackURLRejectsNonlocalAndEmbeddedCredentials(t *testing.T) {
	for _, raw := range []string{"http://127.0.0.1:18766/v1", "http://localhost:9000/v1", "https://[::1]:9443/v1"} {
		if _, err := ValidateLoopbackURL(raw); err != nil {
			t.Fatalf("valid loopback rejected: %s", raw)
		}
	}
	for _, raw := range []string{"https://example.com/v1", "http://127.0.0.2/v1", "http://localhost.evil/v1", "http://localhost./v1", "http://127.1/v1", "http://user:secret@127.0.0.1/v1", "http://127.0.0.1/v1?token=secret", "http://127.0.0.1/v1?", "http://127.0.0.1/v1#secret", "file:///tmp/service", "http://[::ffff:127.0.0.1]/v1"} {
		if _, err := ValidateLoopbackURL(raw); err == nil {
			t.Fatalf("unsafe address accepted: %s", raw)
		}
	}
}

func TestLoopbackHTTPClientIgnoresProxyAndDoesNotFollowRedirect(t *testing.T) {
	var redirected atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { redirected.Add(1); w.WriteHeader(http.StatusOK) }))
	defer target.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture-secret" {
			t.Error("origin credential missing")
		}
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer origin.Close()
	t.Setenv("HTTP_PROXY", "http://127.0.0.1:1")
	client := LoopbackHTTPClient(time.Second)
	request, _ := http.NewRequest(http.MethodGet, strings.Replace(origin.URL, "127.0.0.1", "localhost", 1), nil)
	request.Header.Set("Authorization", "Bearer fixture-secret")
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusTemporaryRedirect || redirected.Load() != 0 {
		t.Fatal("credential-bearing redirect was followed")
	}
}
