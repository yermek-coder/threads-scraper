package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

const maxRequestBody = 64 * 1024

var phoneSeparators = regexp.MustCompile(`[\s().-]`)

type bridge struct {
	// sessionMu serializes send, reconnect, and logout operations. mu protects the short-lived
	// service state so status and QR reads remain available during the logout request.
	sessionMu sync.RWMutex
	mu        sync.RWMutex

	ctx       context.Context
	cancel    context.CancelFunc
	container *sqlstore.Container
	client    *whatsmeow.Client
	paired    bool
	phone     string

	qrPNG        []byte
	qrExpiresAt  time.Time
	qrCancel     context.CancelFunc
	qrGeneration uint64

	retryClient *whatsmeow.Client
	closed      bool
}

type statusResponse struct {
	Connected bool   `json:"connected"`
	LoggedIn  bool   `json:"logged_in"`
	Phone     string `json:"phone"`
}

type sendRequest struct {
	Phone   string `json:"phone"`
	Message string `json:"message"`
}

type sendResponse struct {
	Sent      bool   `json:"sent"`
	MessageID string `json:"message_id"`
}

type errorResponse struct {
	Error string `json:"error"`
}

func main() {
	port := strings.TrimSpace(os.Getenv("PORT"))
	if port == "" {
		port = "8080"
	}
	if strings.HasPrefix(port, ":") {
		port = strings.TrimPrefix(port, ":")
	}
	dataDir := strings.TrimSpace(os.Getenv("DATA_DIR"))
	if dataDir == "" {
		dataDir = "./data"
	}

	service, err := newBridge(dataDir)
	if err != nil {
		log.Fatalf("initialize WhatsApp bridge: %v", err)
	}

	server := &http.Server{
		Addr:              ":" + port,
		Handler:           routes(service),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	serverErrors := make(chan error, 1)
	go func() {
		log.Printf("WhatsApp bridge listening on :%s", port)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErrors <- err
		}
	}()

	select {
	case <-ctx.Done():
	case err := <-serverErrors:
		log.Printf("HTTP server failed: %v", err)
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Printf("HTTP server shutdown: %v", err)
	}
	service.close()
}

func newBridge(dataDir string) (*bridge, error) {
	if err := os.MkdirAll(dataDir, 0o700); err != nil {
		return nil, fmt.Errorf("create data directory: %w", err)
	}
	databasePath, err := filepath.Abs(filepath.Join(dataDir, "whatsapp.db"))
	if err != nil {
		return nil, fmt.Errorf("resolve SQLite path: %w", err)
	}
	// Keep the SQLite connection private to the bridge and persist it on the mounted data volume.
	dsn := fmt.Sprintf("file:%s?_foreign_keys=on&_busy_timeout=5000&_journal_mode=WAL", filepath.ToSlash(databasePath))
	dbLogger := waLog.Stdout("WhatsApp/DB", "ERROR", true)
	container, err := sqlstore.New(context.Background(), "sqlite3", dsn, dbLogger)
	if err != nil {
		return nil, fmt.Errorf("open WhatsApp SQLite store: %w", err)
	}

	device, err := container.GetFirstDevice(context.Background())
	if err != nil {
		_ = container.Close()
		return nil, fmt.Errorf("load WhatsApp session: %w", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	service := &bridge{ctx: ctx, cancel: cancel, container: container}
	service.client = service.newClient(device)
	if device.ID != nil {
		service.paired = true
		service.phone = device.ID.User
	}

	if err := service.connect(); err != nil {
		log.Printf("initial WhatsApp connection failed; will retry: %v", err)
		service.scheduleReconnect(service.client)
	}
	return service, nil
}

func (b *bridge) newClient(device *store.Device) *whatsmeow.Client {
	client := whatsmeow.NewClient(device, waLog.Stdout("WhatsApp", "INFO", true))
	client.BackgroundEventCtx = b.ctx
	client.InitialAutoReconnect = true
	client.AddEventHandler(func(evt any) {
		switch event := evt.(type) {
		case *events.PairSuccess:
			b.mu.Lock()
			if b.client == client {
				b.paired = true
				b.phone = event.ID.User
			}
			b.mu.Unlock()
		case *events.LoggedOut:
			b.mu.Lock()
			currentClient := b.client == client && !b.closed
			if currentClient {
				b.paired = false
				b.phone = ""
				b.clearQRLocked()
			}
			b.mu.Unlock()
			if currentClient {
				go b.resetAfterRemoteLogout(client)
			}
		}
	})
	return client
}

func (b *bridge) connect() error {
	b.sessionMu.Lock()
	b.mu.Lock()
	client := b.client
	err := b.connectLocked(client)
	shouldRetry := err != nil || (client != nil && !client.IsConnected() && !b.paired)
	b.mu.Unlock()
	b.sessionMu.Unlock()
	if shouldRetry {
		b.scheduleReconnect(client)
	}
	return err
}

// connectLocked expects b.mu to be held. QR monitoring is started before Connect,
// as required by whatsmeow's pairing flow.
func (b *bridge) connectLocked(client *whatsmeow.Client) error {
	if b.closed || client == nil || client.IsConnected() {
		return nil
	}
	if client.Store.ID == nil && b.qrCancel == nil {
		qrCtx, cancel := context.WithCancel(b.ctx)
		qrChannel, err := client.GetQRChannel(qrCtx)
		if err != nil {
			cancel()
			return fmt.Errorf("prepare WhatsApp QR channel: %w", err)
		}
		b.qrGeneration++
		generation := b.qrGeneration
		b.qrCancel = cancel
		b.qrPNG = nil
		b.qrExpiresAt = time.Time{}
		go b.consumeQR(client, qrChannel, generation)
	}
	if err := client.Connect(); err != nil {
		b.clearQRLocked()
		return fmt.Errorf("connect WhatsApp client: %w", err)
	}
	return nil
}

func (b *bridge) consumeQR(client *whatsmeow.Client, qrChannel <-chan whatsmeow.QRChannelItem, generation uint64) {
	for item := range qrChannel {
		switch item.Event {
		case whatsmeow.QRChannelEventCode:
			png, err := qrcode.Encode(item.Code, qrcode.Medium, 320)
			if err != nil {
				log.Printf("generate WhatsApp QR image: %v", err)
				continue
			}
			b.mu.Lock()
			if b.client == client && b.qrGeneration == generation {
				b.qrPNG = png
				b.qrExpiresAt = time.Now().Add(item.Timeout)
			}
			b.mu.Unlock()
		case whatsmeow.QRChannelEventError:
			if item.Error != nil {
				log.Printf("WhatsApp pairing error: %v", item.Error)
			}
		case "success":
			log.Printf("WhatsApp device pairing completed")
		}
	}

	b.mu.Lock()
	if b.client == client && b.qrGeneration == generation {
		if b.qrCancel != nil {
			b.qrCancel()
			b.qrCancel = nil
		}
		b.qrPNG = nil
		b.qrExpiresAt = time.Time{}
		shouldReconnect := !b.closed && !b.paired
		b.mu.Unlock()
		if shouldReconnect {
			b.scheduleReconnect(client)
		}
		return
	}
	b.mu.Unlock()
}

func (b *bridge) scheduleReconnect(client *whatsmeow.Client) {
	b.mu.Lock()
	if b.closed || b.retryClient == client || b.client != client {
		b.mu.Unlock()
		return
	}
	b.retryClient = client
	b.mu.Unlock()

	go func() {
		defer func() {
			b.mu.Lock()
			if b.retryClient == client {
				b.retryClient = nil
			}
			b.mu.Unlock()
		}()

		for {
			timer := time.NewTimer(5 * time.Second)
			select {
			case <-timer.C:
			case <-b.ctx.Done():
				if !timer.Stop() {
					<-timer.C
				}
				return
			}

			b.sessionMu.Lock()
			b.mu.Lock()
			if b.closed || b.client != client || client.IsConnected() {
				b.mu.Unlock()
				b.sessionMu.Unlock()
				return
			}
			err := b.connectLocked(client)
			connected := client.IsConnected()
			b.mu.Unlock()
			b.sessionMu.Unlock()

			if err != nil {
				log.Printf("WhatsApp reconnect failed; will retry: %v", err)
			}
			if connected {
				return
			}
		}
	}()
}

func (b *bridge) status() statusResponse {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return statusResponse{
		Connected: b.client != nil && b.client.IsConnected(),
		LoggedIn:  b.paired,
		Phone:     b.phone,
	}
}

func (b *bridge) qrImage() ([]byte, bool) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if len(b.qrPNG) == 0 || time.Now().After(b.qrExpiresAt) {
		return nil, false
	}
	return append([]byte(nil), b.qrPNG...), true
}

func (b *bridge) send(ctx context.Context, request sendRequest) (sendResponse, error) {
	phone, err := normalizePhone(request.Phone)
	if err != nil {
		return sendResponse{}, err
	}
	message := strings.TrimSpace(request.Message)
	if message == "" {
		return sendResponse{}, errors.New("message is required")
	}
	if len([]rune(message)) > 4096 {
		return sendResponse{}, errors.New("message must be 4096 characters or fewer")
	}

	b.sessionMu.RLock()
	defer b.sessionMu.RUnlock()
	b.mu.RLock()
	defer b.mu.RUnlock()
	if b.client == nil || !b.client.IsLoggedIn() {
		return sendResponse{}, errors.New("WhatsApp is not connected")
	}
	jid := types.NewJID(phone, types.DefaultUserServer)
	result, err := b.client.SendMessage(ctx, jid, &waE2E.Message{Conversation: proto.String(message)})
	if err != nil {
		return sendResponse{}, fmt.Errorf("send WhatsApp message: %w", err)
	}
	return sendResponse{Sent: true, MessageID: result.ID}, nil
}

func normalizePhone(value string) (string, error) {
	value = strings.TrimSpace(value)
	if strings.HasPrefix(value, "+") {
		value = value[1:]
	}
	value = phoneSeparators.ReplaceAllString(value, "")
	if len(value) < 8 || len(value) > 15 {
		return "", errors.New("phone must include an international country code and 8 to 15 digits")
	}
	for _, digit := range value {
		if digit < '0' || digit > '9' {
			return "", errors.New("phone must contain only digits and common phone separators")
		}
	}
	return value, nil
}

func (b *bridge) logout(ctx context.Context) (bool, error) {
	b.sessionMu.Lock()
	defer b.sessionMu.Unlock()
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return false, errors.New("bridge is shutting down")
	}
	client := b.client
	b.cancelQRLocked()
	b.qrPNG = nil
	b.qrExpiresAt = time.Time{}
	b.mu.Unlock()

	remoteLogout := false
	if client != nil {
		if client.Store.ID != nil {
			if client.IsLoggedIn() {
				if err := client.Logout(ctx); err == nil {
					remoteLogout = true
				} else {
					log.Printf("remote WhatsApp logout failed; clearing local session: %v", err)
					client.Disconnect()
					if deleteErr := client.Store.Delete(ctx); deleteErr != nil {
						return false, fmt.Errorf("clear local WhatsApp session: %w", deleteErr)
					}
				}
			} else {
				client.Disconnect()
				if err := client.Store.Delete(ctx); err != nil {
					return false, fmt.Errorf("clear local WhatsApp session: %w", err)
				}
			}
		} else {
			client.Disconnect()
		}
	}

	device, err := b.container.GetFirstDevice(ctx)
	if err != nil {
		return remoteLogout, fmt.Errorf("create a fresh WhatsApp session: %w", err)
	}
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return remoteLogout, errors.New("bridge is shutting down")
	}
	b.paired = false
	b.phone = ""
	b.client = b.newClient(device)
	newClient := b.client
	if err := b.connectLocked(newClient); err != nil {
		log.Printf("start WhatsApp pairing after logout: %v", err)
	}
	b.mu.Unlock()
	go b.scheduleReconnect(newClient)
	return remoteLogout, nil
}

// The pinned whatsmeow fork deletes its device row before emitting LoggedOut, but the
// event is dispatched asynchronously. Wait until the store reflects that deletion,
// then replace the deleted client so a fresh QR flow can begin without an API call.
func (b *bridge) resetAfterRemoteLogout(oldClient *whatsmeow.Client) {
	b.sessionMu.Lock()
	defer b.sessionMu.Unlock()

	ctx, cancel := context.WithTimeout(b.ctx, 10*time.Second)
	defer cancel()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	var device *store.Device
	for {
		b.mu.RLock()
		stillCurrent := !b.closed && b.client == oldClient
		b.mu.RUnlock()
		if !stillCurrent {
			return
		}

		var err error
		device, err = b.container.GetFirstDevice(ctx)
		if err == nil && device.ID == nil {
			break
		}

		select {
		case <-ticker.C:
		case <-ctx.Done():
			if err != nil {
				log.Printf("wait for WhatsApp session deletion: %v", err)
			} else {
				log.Printf("WhatsApp session was not cleared after remote logout")
			}
			return
		}
	}

	b.mu.Lock()
	if b.closed || b.client != oldClient {
		b.mu.Unlock()
		return
	}
	b.paired = false
	b.phone = ""
	b.client = b.newClient(device)
	newClient := b.client
	err := b.connectLocked(newClient)
	b.mu.Unlock()
	if err != nil {
		log.Printf("start WhatsApp pairing after remote logout: %v", err)
	}
	go b.scheduleReconnect(newClient)
}

func (b *bridge) cancelQRLocked() {
	b.qrGeneration++
	if b.qrCancel != nil {
		b.qrCancel()
		b.qrCancel = nil
	}
}

func (b *bridge) clearQRLocked() {
	b.cancelQRLocked()
	b.qrPNG = nil
	b.qrExpiresAt = time.Time{}
}

func (b *bridge) close() {
	b.sessionMu.Lock()
	defer b.sessionMu.Unlock()
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return
	}
	b.closed = true
	b.clearQRLocked()
	if b.cancel != nil {
		b.cancel()
	}
	client := b.client
	container := b.container
	b.mu.Unlock()
	if client != nil {
		client.Disconnect()
	}
	if container != nil {
		if err := container.Close(); err != nil {
			log.Printf("close WhatsApp SQLite store: %v", err)
		}
	}
}

func routes(service *bridge) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /api/status", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, service.status())
	})
	mux.HandleFunc("GET /api/qr", func(w http.ResponseWriter, _ *http.Request) {
		image, ok := service.qrImage()
		if !ok {
			writeError(w, http.StatusConflict, "QR code is not available; wait for WhatsApp to connect")
			return
		}
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(image)
	})
	mux.HandleFunc("POST /api/send", func(w http.ResponseWriter, r *http.Request) {
		var request sendRequest
		if err := decodeJSON(w, r, &request); err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		response, err := service.send(r.Context(), request)
		if err != nil {
			status := http.StatusBadRequest
			if strings.Contains(err.Error(), "not connected") {
				status = http.StatusServiceUnavailable
			}
			writeError(w, status, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, response)
	})
	mux.HandleFunc("POST /api/logout", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		remoteLogout, err := service.logout(ctx)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]bool{"logged_out": true, "remote_logout": remoteLogout})
	})

	return requestLogger(mux)
}

func decodeJSON(w http.ResponseWriter, r *http.Request, value any) error {
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBody)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return fmt.Errorf("invalid JSON request: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("request must contain exactly one JSON object")
		}
		return fmt.Errorf("invalid trailing JSON: %w", err)
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Printf("write JSON response: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, errorResponse{Error: message})
}

func requestLogger(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(started).Round(time.Millisecond))
	})
}
