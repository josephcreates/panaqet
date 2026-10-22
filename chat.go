package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"github.com/gorilla/websocket"
)

// WebSocket upgrader
var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// Connected clients
var clients = make(map[*websocket.Conn]bool)
var broadcast = make(chan Message)

// Message struct (Ensure IDs are strings)
type Message struct {
    ConversationID string `json:"conversation_id"`
    SenderID       string `json:"sender_id"`
    SenderRole     string `json:"sender_role"`
    Content        string `json:"content"`
}


// Handle new WebSocket connections
func handleConnections(w http.ResponseWriter, r *http.Request) {
    ws, err := upgrader.Upgrade(w, r, nil)
    if err != nil {
        log.Println("❌ WebSocket Error:", err)
        return
    }
    defer ws.Close()

    clients[ws] = true
    log.Println("✅ WebSocket Connected")

    for {
        var msg Message
        err := ws.ReadJSON(&msg)
        if err != nil {
            log.Println("❌ Read Error:", err)
            delete(clients, ws)
            break
        }

        // ✅ Convert conversation_id to integer safely
        conversationID, err := strconv.Atoi(msg.ConversationID)
        if err != nil {
            log.Println("❌ Invalid conversation_id:", msg.ConversationID)
            continue
        }

        senderID, err := strconv.Atoi(msg.SenderID)
        if err != nil {
            log.Println("❌ Invalid sender_id:", msg.SenderID)
            continue
        }

        log.Printf("🔹 Received message: Conversation ID: %d, Sender ID: %d, Content: %s", conversationID, senderID, msg.Content)

        // ✅ Send to Flask
        msg.ConversationID = strconv.Itoa(conversationID)  // Convert back to string
        msg.SenderID = strconv.Itoa(senderID)  // Convert back to string
        broadcast <- msg

        // ✅ Save message to Flask database
        saveMessageToFlask(msg)
    }
}



// Save chat message to Flask (Port 5000)
func saveMessageToFlask(msg Message) {
	jsonData, _ := json.Marshal(msg)
	fmt.Println("📤 Sending message to Flask:", string(jsonData))  // ✅ Log before sending

	resp, err := http.Post("http://localhost:5000/chat/save_message", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		log.Println("❌ Failed to send message to Flask:", err)
		return
	}
	defer resp.Body.Close()

	fmt.Printf("✅ Flask response status: %d\n", resp.StatusCode)
}


// Broadcast messages to all clients
func handleMessages() {
	for {
		msg := <-broadcast
		log.Println("📢 Broadcasting message:", msg)

		for client := range clients {
			err := client.WriteJSON(msg)
			if err != nil {
				log.Println("❌ Write Error:", err)
				client.Close()
				delete(clients, client)
			}
		}
	}
}

func main() {
	go handleMessages()
	http.HandleFunc("/ws", handleConnections)
	fmt.Println("Go WebSocket Server started on :8080")
	log.Fatal(http.ListenAndServe(":8080", nil))
}
