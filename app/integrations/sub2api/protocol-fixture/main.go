// This executable is a bounded Redis protocol fixture, never a Redis deployment.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/alicebob/miniredis/v2"
)

type configuration struct {
	ProtocolTest bool   `json:"protocol_test"`
	Address      string `json:"address"`
	Password     string `json:"password"`
}

func run() error {
	path := flag.String("config", "", "absolute private fixture configuration")
	flag.Parse()
	if !filepath.IsAbs(*path) {
		return errors.New("fixture_configuration_required")
	}
	file, err := os.Open(*path)
	if err != nil {
		return errors.New("fixture_configuration_unavailable")
	}
	defer file.Close()
	if info, err := file.Stat(); err != nil || info.Size() > 4096 {
		return errors.New("fixture_configuration_size")
	}
	decoder := json.NewDecoder(file)
	decoder.DisallowUnknownFields()
	var config configuration
	if decoder.Decode(&config) != nil || decoder.Decode(new(any)) != io.EOF {
		return errors.New("fixture_configuration_invalid")
	}
	host, port, err := net.SplitHostPort(config.Address)
	if err != nil || host != "127.0.0.1" || port == "0" || !config.ProtocolTest || len(config.Password) < 43 || len(config.Password) > 128 {
		return errors.New("fixture_loopback_and_auth_required")
	}
	server := miniredis.NewMiniRedis()
	server.RequireAuth(config.Password)
	if server.StartAddr(config.Address) != nil {
		return errors.New("fixture_listen_failed")
	}
	defer server.Close()
	fmt.Println(`{"event":"redis_protocol_fixture_ready","production_redis":false}`)
	stopped := make(chan os.Signal, 1)
	signal.Notify(stopped, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(stopped)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			server.FastForward(time.Second)
		case <-stopped:
			return nil
		}
	}
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
