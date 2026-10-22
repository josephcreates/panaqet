from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from models import db, Conversation, Message

# Create a new blueprint for chat-related routes
chat_bp = Blueprint('chat_routes', __name__)

@chat_bp.route('/chat/<int:conversation_id>')
@login_required
def chat(conversation_id):
    """
    Loads the chat page for a specific conversation.
    """
    conversation = Conversation.query.get_or_404(conversation_id)
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp).all()

    return render_template("chat.html", conversation=conversation, messages=messages)


@chat_bp.route("/chat/chat_with_seller/<int:product_id>/<int:seller_id>")
@login_required
def chat_with_seller(product_id, seller_id):
    """Start or retrieve a chat between buyer and seller for a product."""
    if current_user.id == seller_id:
        return redirect(url_for("chat_routes.chat", conversation_id=None))

    # Check if a conversation already exists
    conversation = Conversation.query.filter_by(
        buyer_id=current_user.id, seller_id=seller_id, product_id=product_id
    ).first()

    # ✅ Log conversation creation
    if conversation:
        print(f"✅ Existing conversation found: {conversation.id}")
    else:
        print("🚀 Creating new conversation...")

    # If no conversation exists, create one
    if not conversation:
        conversation = Conversation(
            buyer_id=current_user.id,
            seller_id=seller_id,
            product_id=product_id
        )
        db.session.add(conversation)
        db.session.commit()
        print(f"✅ New conversation created: {conversation.id}")

    return redirect(url_for("chat_routes.chat", conversation_id=conversation.id))

@chat_bp.route("/chat/get_messages/<int:conversation_id>")
@login_required
def get_messages(conversation_id):
    """Fetch all messages for a specific conversation."""
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        print(f"❌ Conversation {conversation_id} not found!")
        return jsonify({"error": "Conversation not found"}), 404

    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp).all()

    return jsonify([
        {
            "id": msg.id,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender.username,
            "content": msg.content,
            "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        } for msg in messages
    ])


@chat_bp.route("/chat/save_message", methods=["POST"])
@login_required
def save_message():
    """Saves messages received from WebSocket to the database."""
    data = request.get_json()

    print("🔹 Received message:", data)

    conversation_id = data.get("conversation_id")
    sender_id = data.get("sender_id")
    sender_role = data.get("sender_role")
    content = data.get("content")

    if not conversation_id or not sender_id or not content:
        print("❌ Missing required fields!")
        return jsonify({"error": "Missing required fields"}), 400

    # Ensure conversation exists
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        print(f"❌ Conversation {conversation_id} not found!")
        return jsonify({"error": "Conversation not found"}), 404

    # Save message
    new_message = Message(
        conversation_id=conversation_id,
        sender_id=sender_id,
        sender_role=sender_role,
        content=content,
    )

    db.session.add(new_message)
    db.session.commit()

    print(f"✅ Message saved to database: {content}")
    return jsonify({"success": "Message saved successfully"}), 200

import sys
sys.stdout.reconfigure(encoding='utf-8')  # Set console encoding to UTF-8

print("🚀 Creating new conversation...")
