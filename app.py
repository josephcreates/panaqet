from flask import Flask, send_from_directory, send_file, render_template, redirect, url_for, flash, request, session, abort, Blueprint, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
from threading import Thread
import os
import requests
import webbrowser
from models import User, Cart, Product, ProductComponent, ProductImage, Buyer, Seller, Subscription, Admin, Message, Conversation
from admin_routes import admin_bp
from seller_routes import seller_bp
from buyer_routes import buyer_bp
from chat_routes import chat_bp
from affiliate_routes import affiliate_bp
from database import db
from routes import routes


app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SESSION_TYPE'] = 'filesystem'  # Store sessions in the filesystem
app.config['SESSION_COOKIE_NAME'] = 'your_session_cookie_name'
app.config['SESSION_PERMANENT'] = False  # Ensure sessions are temporary unless 'remember' is set
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SECRET_KEY'] = os.urandom(24)  # Randomly generate a secure secret key
app.config['RECAPTCHA_PUBLIC_KEY'] = os.environ.get('RECAPTCHA_PUBLIC_KEY')
app.config['RECAPTCHA_PRIVATE_KEY'] = os.environ.get('RECAPTCHA_PRIVATE_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI', 'sqlite:///site.db')  # SQLite by default
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # Set file size limit to 500MB
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi', 'mov'}
app.config['STORE_LOGOS_FOLDER'] = os.path.join(app.root_path, 'static', 'store_logos')
app.config['LANGUAGES'] = ['en', 'fr', 'pt', 'it', 'es']  # Add other languages as needed


csrf = CSRFProtect(app)
Session(app)


# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)

# Register blueprints
app.register_blueprint(routes)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(seller_bp, url_prefix='/seller')
app.register_blueprint(buyer_bp, url_prefix='/buyer')
app.register_blueprint(affiliate_bp, url_prefix='/affiliate')
app.register_blueprint(chat_bp, url_prefix='/chat')

#@login_manager.user_loader
#def load_user(user_id):
#    with db.session() as session:
#       return session.get(User, int(user_id))

# Redirect unauthorized users to custom login page
@login_manager.unauthorized_handler
def unauthorized():
    flash("You need to log in to access this page.", "warning")
    return redirect(url_for('routes.access_denied', next=request.url))

@login_manager.user_loader
def load_user(user_id):
    # Check if session is for admin or regular user
    if session.get("user_type") == "admin":
        return db.session.get(Admin, int(user_id))
    return db.session.get(User, int(user_id))

def get_product_by_id(product_id):
    return Product.query.get(product_id)

# Define the folder for saving ID images
ID_IMAGES_FOLDER = 'uploads/id_images'
os.makedirs(ID_IMAGES_FOLDER, exist_ok=True)
app.config['ID_IMAGES_FOLDER'] = ID_IMAGES_FOLDER

# Define the folder for saving receipts
RECEIPTS_FOLDER = 'receipts'
os.makedirs(RECEIPTS_FOLDER, exist_ok=True)


initialization_done = False  # Global flag to track initialization


@app.before_request
def initialize_database():
    """
    Ensures that tables are created, subscription plans are seeded,
    and the admin user is created only once when the app starts.
    """
    global initialization_done

    if not initialization_done:
        db.create_all()  # Create all tables
        seed_subscription_plans()  # Seed the subscription plans
        initialization_done = True  # Mark initialization as done

    
@app.after_request
def add_header(response):
    """
    Adds a cache-control header to responses to prevent caching.
    """
    response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def inject_theme():
    """
    Injects the user's theme into templates if available.
    Ensures the theme is always a string.
    """
    theme = session.get('user_theme', 'light')
    if not isinstance(theme, str):  
        theme = 'light'  # Fallback to default
    return dict(user_theme=theme)


@app.context_processor
def inject_cart_count():
    """
    Injects the cart count into templates for authenticated users.
    """
    cart_count = (
        Cart.query.filter_by(user_id=current_user.id).count()
        if current_user.is_authenticated else 0
    )
    return {'cart_count': cart_count}


@app.errorhandler(404)
def page_not_found(e):
    """
    Renders a custom 404 error page.
    """
    return render_template('404.html'), 404


def verify_recaptcha(response):
    """
    Verifies the reCAPTCHA response with Google's API.
    """
    url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {
        'secret': app.config['RECAPTCHA_PRIVATE_KEY'],
        'response': response
    }
    r = requests.post(url, data=payload)
    return r.json().get('success', False)


def seed_subscription_plans():
    """
    Seeds the database with predefined subscription plans.
    """
    plans = [
        {
            "name": "Basic Plan",
            "description": "Ideal for new sellers just starting out.",
            "price": 50.0,
            "validity_period": 30,
            "features": {
                "max_products": 10,
                "support": "Email only",
                "analytics": "Basic (views and clicks)",
            },
        },
        {
            "name": "Standard Plan",
            "description": "For growing sellers who want more visibility.",
            "price": 120.0,
            "validity_period": 90,
            "features": {
                "max_products": 50,
                "support": "Email and chat",
                "analytics": "Advanced (views, clicks, conversions)",
                "priority_placement": True,
            },
        },
        {
            "name": "Premium Plan",
            "description": "Best for established sellers looking to scale.",
            "price": 300.0,
            "validity_period": 180,
            "features": {
                "max_products": "Unlimited",
                "support": "Dedicated account manager",
                "analytics": "Full suite (views, clicks, conversions, revenue tracking)",
                "promoted_products": True,
            },
        },
        {
            "name": "Seasonal Boost Plan",
            "description": "Perfect for sellers aiming for seasonal sales spikes.",
            "price": 150.0,
            "validity_period": 30,
            "features": {
                "additional_products": 20,
                "priority_placement": "Seasonal",
                "social_media_promotions": True,
            },
        },
        {
            "name": "Enterprise Plan",
            "description": "Designed for large-scale sellers with extensive needs.",
            "price": 2000.0,
            "validity_period": 365,
            "features": {
                "max_products": "Unlimited",
                "support": "Multi-user and API access",
                "analytics": "Custom insights",
                "personalized_promotions": True,
            },
        },
        {
            "name": "Trial Plan",
            "description": "A free or low-cost option to attract new sellers.",
            "price": 0.0,
            "validity_period": 14,
            "features": {
                "max_products": 5,
                "support": "None",
                "analytics": "Basic (views and clicks)",
            },
        },
        {
            "name": "Pay-Per-Feature Plan",
            "description": "Flexible pricing based on additional features.",
            "price": 50.0,  # Base price
            "validity_period": 30,
            "features": {
                "base_benefits": "Basic Plan",
                "add_ons": ["Extra listings", "Priority placement", "Advanced analytics"],
            },
        },
    ]

    # Add plans to the database if they don't already exist
    for plan in plans:
        existing_plan = Subscription.query.filter_by(name=plan["name"]).first()
        if not existing_plan:
            new_plan = Subscription(
                name=plan["name"],
                description=plan["description"],
                price=plan["price"],
                validity_period=plan["validity_period"],
                features=plan["features"],
                status="active"
            )
            db.session.add(new_plan)
    db.session.commit()

@app.route('/save_message', methods=['POST'])
def save_message():
    data = request.json

    # Ensure conversation exists (or create one if needed)
    conversation = Conversation.query.get(data['conversation_id'])
    if not conversation:
        conversation = Conversation(user_id=data['sender_id'])
        db.session.add(conversation)
        db.session.commit()

    # Store the message
    new_message = Message(
        sender_id=data['sender_id'],
        sender_role="user",
        content=data['content'],
        conversation_id=conversation.id
    )
    db.session.add(new_message)
    db.session.commit()

    return jsonify({"status": "success"})

@app.route('/get_conversation', methods=['POST'])
def get_conversation():
    """
    Retrieve or create a conversation between a buyer and seller for a specific product.
    """
    data = request.json
    buyer_id = data.get('buyer_id')
    seller_id = data.get('seller_id')
    product_id = data.get('product_id')

    # Check if conversation already exists
    conversation = Conversation.query.filter_by(
        buyer_id=buyer_id, seller_id=seller_id, product_id=product_id
    ).first()

    # If no conversation exists, create a new one
    if not conversation:
        conversation = Conversation(buyer_id=buyer_id, seller_id=seller_id, product_id=product_id)
        db.session.add(conversation)
        db.session.commit()

    return jsonify({"conversation_id": conversation.id})

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

#-----------
if __name__ == '__main__':
    # Ensure the database is initialized before the app runs
    with app.app_context():
        initialize_database()

    # Run Flask in a separate thread and pywebview in the main thread
    flask_thread = Thread(target=lambda: app.run(debug=True, use_reloader=False))
    flask_thread.start()

    # Open the web application in the default browser (optional)
    webbrowser.open('http://127.0.0.1:5000')
