package outbound

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ValidateLoopbackURL is deliberately narrower than ordinary model relays.
// Settings credentials are sent only to an explicitly configured local origin.
func ValidateLoopbackURL(raw string) (*url.URL, error) {
	if len(raw) > 4096 {
		return nil, errors.New("local service address is too long")
	}
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Opaque != "" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" {
		return nil, errors.New("invalid local service address")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, errors.New("invalid local service scheme")
	}
	if !literalLoopbackHost(parsed.Hostname()) {
		return nil, errors.New("local service must use a loopback host")
	}
	return parsed, nil
}

func literalLoopbackHost(host string) bool {
	switch strings.ToLower(host) {
	case "127.0.0.1", "localhost", "::1":
		return true
	default:
		return false
	}
}

// LoopbackHTTPClient never uses DNS or environment proxies and never follows a
// redirect. A stored credential therefore cannot leave the configured origin.
func LoopbackHTTPClient(timeout time.Duration) *http.Client {
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	transport := &http.Transport{
		Proxy: nil, TLSHandshakeTimeout: 5 * time.Second,
		MaxIdleConns: 8, IdleConnTimeout: 30 * time.Second,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(address)
			if err != nil || !literalLoopbackHost(host) {
				return nil, errors.New("local service dial rejected")
			}
			if strings.EqualFold(host, "localhost") {
				conn, firstErr := dialer.DialContext(ctx, network, net.JoinHostPort("127.0.0.1", port))
				if firstErr == nil {
					return conn, nil
				}
				return dialer.DialContext(ctx, network, net.JoinHostPort("::1", port))
			}
			return dialer.DialContext(ctx, network, net.JoinHostPort(host, port))
		},
	}
	return &http.Client{Transport: transport, Timeout: timeout,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
}
