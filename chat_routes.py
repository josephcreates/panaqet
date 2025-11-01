from flask import Blueprint, render_template, redirect, request, url_for, flash, jsonify, make_response, current_app
from flask_login import login_required, current_user
from datetime import datetime
from database import db
from models import Conversation, Message, Product, Buyer, Seller, User
import json

chat_bp = Blueprint('chat_routes', __name__, template_folder='templates')

@chat_bp.route('/start/<int:product_id>')
@login_required
def start_conversation(product_id):
    """Buyer starts or resumes a conversation with seller about a product."""
    product = Product.query.get_or_404(product_id)

    if not current_user.is_buyer:
        flash("Only buyers can initiate a chat with sellers.", "warning")
        return redirect(url_for('seller_routes.product_details', product_id=product_id))

    buyer = Buyer.query.filter_by(user_id=current_user.id).first()
    seller = Seller.query.filter_by(id=product.seller_id).first()

    if not buyer or not seller:
        flash("Invalid chat participants.", "danger")
        return redirect(url_for('seller_routes.product_details', product_id=product_id))

    # Check if conversation already exists
    convo = Conversation.query.filter_by(
        buyer_id=buyer.id,
        seller_id=seller.id,
        product_id=product.id
    ).first()

    if not convo:
        convo = Conversation(buyer_id=buyer.id, seller_id=seller.id, product_id=product.id)
        db.session.add(convo)
        db.session.commit()

        # --- Send initial predefined message ---
        initial_msg = Message(
            conversation_id=convo.id,
            sender_id=current_user.id,
            sender_role=current_user.role,
            content="Hi, I'm interested in this product!",
            timestamp=datetime.utcnow()
        )
        db.session.add(initial_msg)
        db.session.commit()

    else:
        # If conversation exists but has no messages, send initial message
        if not convo.messages:
            initial_msg = Message(
                conversation_id=convo.id,
                sender_id=current_user.id,
                sender_role=current_user.role,
                content="Hi, I'm interested in this product!",
                timestamp=datetime.utcnow()
            )
            db.session.add(initial_msg)
            db.session.commit()

    return redirect(url_for('chat_routes.view_conversation', conversation_id=convo.id))

@chat_bp.route('/list/<int:user_id>')
@login_required
def list_conversations(user_id):
    """Return all conversations for the current user."""
    if current_user.id != user_id:
        return jsonify({"error": "Unauthorized"}), 403

    # Determine if user is buyer or seller
    if current_user.is_buyer and current_user.buyer_profile:
        conversations = Conversation.query.filter_by(buyer_id=current_user.buyer_profile.id)
    elif current_user.is_seller and current_user.seller_relationship:
        conversations = Conversation.query.filter_by(seller_id=current_user.seller_relationship.id)
    else:
        return jsonify([])

    # Order by latest message timestamp
    conversations = (
        conversations
        .outerjoin(Message)
        .group_by(Conversation.id)
        .order_by(db.func.max(Message.timestamp).desc())
        .all()
    )

    result = []
    for c in conversations:
        # Determine counterparty
        if current_user.is_buyer:
            counterparty_name = c.seller.username if c.seller else "Seller"
        else:  # seller
            counterparty_name = c.buyer.username if c.buyer else "Buyer"

        # Last message
        last_msg = c.messages[-1] if c.messages else None
        result.append({
            "id": c.id,
            "counterparty_name": counterparty_name,
            "product": {"id": c.product.id, "name": c.product.name} if c.product else None,
            "last_message": {
                "content": last_msg.content if last_msg else "No messages yet",
                "timestamp": last_msg.timestamp.isoformat() if last_msg else None
            },
            "unread_count": 0
        })

    return jsonify(result)


@chat_bp.route('/view/<int:conversation_id>')
@login_required
def view_conversation(conversation_id):
    """Show a conversation thread."""
    convo = Conversation.query.get_or_404(conversation_id)
    product = convo.product
    seller = convo.seller
    buyer = convo.buyer

    # Restrict access
    if current_user.role == 'buyer':
        allowed = (buyer.user_id == current_user.id)
    elif current_user.role == 'seller':
        allowed = (seller.user_id == current_user.id)
    else:
        allowed = False

    if not allowed:
        flash("You are not authorized to view this conversation.", "danger")
        return redirect(url_for('routes.marketplace'))

    # Make the current_user serializable for JS
    js_current_user = {
        "id": current_user.id,
        "username": getattr(current_user, "username", ""),
        "email": getattr(current_user, "email", ""),
        "role": getattr(current_user, "role", "")
    }

    return render_template(
        'chat_page.html',
        convo=convo,
        product=product,
        buyer=buyer,
        seller=seller,
        js_current_user=js_current_user,  # ✅ safe for tojson
    )


@chat_bp.route('/send/<int:conversation_id>', methods=['POST'])
@login_required
def send_message(conversation_id):
    convo = Conversation.query.get_or_404(conversation_id)

    # Ensure current_user is part of conversation
    if not ((convo.buyer and convo.buyer.user_id == current_user.id) or
            (convo.seller and convo.seller.user_id == current_user.id)):
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or request.form or {}
    content = (data.get('content') or data.get('message') or '').strip()

    if not content:
        return jsonify({"error": "Message cannot be empty."}), 400

    try:
        msg = Message(
            conversation_id=convo.id,
            sender_id=current_user.id,
            sender_role=current_user.role,
            content=content,
            timestamp=datetime.utcnow()
        )
        db.session.add(msg)
        db.session.commit()
    except Exception as e:
        # rollback and log full exception so you can see what went wrong
        db.session.rollback()
        current_app.logger.exception(f"Failed saving chat message (conversation_id={conversation_id})")
        return jsonify({"error": "Failed to save message", "details": str(e)}), 500

    resp_payload = {
        "id": msg.id,
        "conversation_id": convo.id,
        "sender_id": msg.sender_id,
        "sender_name": current_user.username,
        "content": msg.content,
        "timestamp": msg.timestamp.isoformat()
    }

    return jsonify(resp_payload), 201

@chat_bp.route('/messages/<int:conversation_id>')
@login_required
def get_messages(conversation_id):
    convo = Conversation.query.get_or_404(conversation_id)

    # Ensure user is part of this conversation
    if not ((convo.buyer and convo.buyer.user_id == current_user.id) or
            (convo.seller and convo.seller.user_id == current_user.id)):
        return jsonify({"error": "Unauthorized"}), 403

    messages = []
    for m in sorted(convo.messages, key=lambda x: x.timestamp):
        # sender_name: always use User table
        sender = User.query.get(m.sender_id)
        messages.append({
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_name": sender.username if sender else "Unknown",
            "content": m.content,
            "timestamp": m.timestamp.isoformat()
        })

    return jsonify(messages)

@chat_bp.route('/chat/read/<int:conversation_id>', methods=['POST'])
@login_required
def mark_as_read(conversation_id):
    convo = Conversation.query.get_or_404(conversation_id)

    # Identify if current user is buyer or seller
    user_role = 'buyer' if getattr(current_user, 'buyer_profile', None) else 'seller'

    # Mark all messages not sent by current user as read
    for msg in convo.messages:
        if msg.sender_id != current_user.id and not msg.is_read:
            msg.is_read = True
            db.session.add(msg)
    db.session.commit()
    return jsonify({'success': True})

# In-memory store for typing status
def get_current_user():
    return current_user

typing_users = {}  # key: conv_id, value: username

@chat_bp.route('/chat/typing/<int:conv_id>', methods=['POST'])
@login_required
def typing(conv_id):
    user = get_current_user()
    typing_users[conv_id] = user.username
    return '', 204

@chat_bp.route('/chat/stop_typing/<int:conv_id>', methods=['POST'])
def stop_typing(conv_id):
    typing_users.pop(conv_id, None)
    return '', 204

@chat_bp.route('/chat/typing_status/<int:conv_id>', methods=['GET'])
def typing_status(conv_id):
    user = get_current_user()
    is_typing = conv_id in typing_users and typing_users[conv_id] != user.username
    return jsonify({'is_typing': is_typing, 'username': typing_users.get(conv_id, '')})
