package setup

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestProbeOK(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer sk-x" {
			w.WriteHeader(401)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	res := probe(context.Background(), srv.URL, "sk-x")
	if !res.OK {
		t.Fatalf("expected ok, got %q", res.Message)
	}
}

func TestProbeRejectsKey(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()
	res := probe(context.Background(), srv.URL, "bad")
	if res.OK {
		t.Fatal("401 should not be ok")
	}
	if !strings.Contains(res.Message, "HTTP 401") {
		t.Fatalf("message = %q", res.Message)
	}
}

func TestProbeUnverifiedButReachable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	res := probe(context.Background(), srv.URL, "sk-x")
	if !res.OK || !strings.Contains(res.Message, "reachable") {
		t.Fatalf("500 should be reachable: %+v", res)
	}
}

func TestProbeNetworkFailure(t *testing.T) {
	res := probe(context.Background(), "http://127.0.0.1:1", "sk-x")
	if res.OK {
		t.Fatal("dead port should fail")
	}
}
