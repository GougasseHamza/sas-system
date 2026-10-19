package main

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/crypto/ssh"
)

func main() {
	if len(os.Args) < 5 {
		fmt.Fprintln(os.Stderr, "usage: ssh-run KEY USER HOST COMMAND...")
		os.Exit(2)
	}
	keyBytes, err := os.ReadFile(os.Args[1])
	check(err)
	signer, err := ssh.ParsePrivateKey(keyBytes)
	check(err)

	fingerprint := ""
	expectedFingerprint := os.Getenv("SAS_ARENA_SSH_HOSTKEY")
	config := &ssh.ClientConfig{
		User: os.Args[2],
		Auth: []ssh.AuthMethod{ssh.PublicKeys(signer)},
		HostKeyCallback: func(hostname string, remote net.Addr, key ssh.PublicKey) error {
			fingerprint = ssh.FingerprintSHA256(key)
			if expectedFingerprint != "" && fingerprint != expectedFingerprint {
				return fmt.Errorf(
					"host key mismatch: expected %s, received %s",
					expectedFingerprint,
					fingerprint,
				)
			}
			return nil
		},
		Timeout: 15 * time.Second,
	}
	client, err := ssh.Dial("tcp", net.JoinHostPort(os.Args[3], "22"), config)
	check(err)
	defer client.Close()

	session, err := client.NewSession()
	check(err)
	defer session.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	session.Stdout = &stdout
	session.Stderr = &stderr
	if os.Args[4] == "--upload" {
		if len(os.Args) != 7 {
			fmt.Fprintln(os.Stderr, "usage: ssh-run KEY USER HOST --upload LOCAL REMOTE")
			os.Exit(2)
		}
		local, openErr := os.Open(os.Args[5])
		check(openErr)
		defer local.Close()
		remote := os.Args[6]
		command := "mkdir -p " + shellQuote(filepath.Dir(remote)) +
			" && cat > " + shellQuote(remote)
		stdin, pipeErr := session.StdinPipe()
		check(pipeErr)
		check(session.Start(command))
		_, copyErr := io.Copy(stdin, local)
		closeErr := stdin.Close()
		if copyErr != nil {
			err = copyErr
		} else if closeErr != nil {
			err = closeErr
		} else {
			err = session.Wait()
		}
	} else {
		err = session.Run(strings.Join(os.Args[4:], " "))
	}
	fmt.Fprintf(os.Stderr, "host-key %s\n", fingerprint)
	_, _ = os.Stdout.Write(stdout.Bytes())
	_, _ = os.Stderr.Write(stderr.Bytes())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
