import datetime
from flask import Flask, send_from_directory, send_file, render_template, redirect, url_for, flash, request, session, abort, Blueprint, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_babel import Babel, _
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
from threading import Thread
import webview
import os
import requests
import webbrowser
from models import Driver, User, Cart, Product, ProductComponent, ProductImage, Buyer, Seller, Subscription, Admin, Message
from admin_routes import admin_bp
from seller_routes import seller_bp
from buyer_routes import buyer_bp
from affiliate_routes import affiliate_bp
from chat_routes import chat_bp
from driver_routes import driver_bp
from wallet import user_bp
from database import db, DATABASE_URI
from routes import routes
from extensions import mail
from flask_socketio import SocketIO
from config import Config


app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SESSION_TYPE'] = 'filesystem'  # Store sessions in the filesystem
app.config['SESSION_COOKIE_NAME'] = 'your_session_cookie_name'
app.config["SESSION_FILE_DIR"] = os.path.join(app.root_path, "flask_sessions")
app.config['SESSION_PERMANENT'] = True # Ensure sessions are temporary unless 'remember' is set
app.config['SESSION_TYPE'] = 'filesystem'
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=7)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config.from_object(Config)

# Flask-Mail configuration (Gmail SMTP)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = "lampteyjoseph860@gmail.com"   # <-- replace with your Gmail
app.config['MAIL_PASSWORD'] = "qmix azcx gwiq pseb"       # <-- your App Password (no spaces)
app.config['MAIL_DEFAULT_SENDER'] = ("PanaQet Support", "lampteyjoseph860@gmail.com")
#qmix azcx gwiq pseb

app.config['RECAPTCHA_PUBLIC_KEY'] = os.environ.get('RECAPTCHA_PUBLIC_KEY')
app.config['RECAPTCHA_PRIVATE_KEY'] = os.environ.get('RECAPTCHA_PRIVATE_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI  # Use the same DB path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # Set file size limit to 500MB
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi', 'mov'}
app.config['STORE_LOGOS_FOLDER'] = os.path.join(app.root_path, 'static', 'store_logos')
app.config['LANGUAGES'] = ['en', 'fr', 'pt', 'it', 'es']  # Add other languages as needed
app.config["ROUTING_SERVICE_URL"] = "http://localhost:8010/route"
app.config['WTF_CSRF_HEADERS'] = ['X-CSRFToken', 'X-CSRF-Token']

os.makedirs(app.config['STORE_LOGOS_FOLDER'], exist_ok=True)
os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)

csrf = CSRFProtect(app)
babel = Babel(app)
socketio = SocketIO(app, cors_allowed_origins="*")
Session(app)


# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.login_view = 'routes.login'   # normal user login view
login_manager.login_message = "Please log in to access this page."
login_manager.init_app(app)
mail.init_app(app)
login_manager.session_protection = "strong"
app.config["SESSION_USE_SIGNER"] = True


# Register blueprints
app.register_blueprint(routes)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(seller_bp, url_prefix='/seller')
app.register_blueprint(buyer_bp, url_prefix='/buyer')
app.register_blueprint(affiliate_bp, url_prefix='/affiliate')
app.register_blueprint(driver_bp, url_prefix='/driver')
app.register_blueprint(user_bp, url_prefix='/user')
app.register_blueprint(chat_bp, url_prefix='/chat')

# Redirect unauthorized users to custom login page
@login_manager.unauthorized_handler
def unauthorized():
    flash("You need to log in to access this page.", "warning")
    return redirect(url_for('routes.access_denied', next=request.url))

@login_manager.user_loader
def load_user(user_id):
    user_type = session.get("user_type")
    try:
        uid = int(user_id)
    except Exception:
        return None

    if user_type == "admin":
        return db.session.get(Admin, uid)
    elif user_type == "driver":
        return db.session.get(Driver, uid)
    else:
        return db.session.get(User, uid)

def get_product_by_id(product_id):
    return Product.query.get(product_id)

# Replace ID_IMAGES_FOLDER setup with an absolute path and ensure directories exist
ID_IMAGES_FOLDER = os.path.join(app.root_path, 'uploads', 'id_images')
os.makedirs(ID_IMAGES_FOLDER, exist_ok=True)
app.config['ID_IMAGES_FOLDER'] = ID_IMAGES_FOLDER

# Ensure receipts folder is absolute too
RECEIPTS_FOLDER = os.path.join(app.root_path, 'receipts')
os.makedirs(RECEIPTS_FOLDER, exist_ok=True)
app.config['RECEIPTS_FOLDER'] = RECEIPTS_FOLDER

# Helper to validate allowed extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


initialization_done = False  # Global flag to track initialization


@app.before_request
def initialize_database():
    """
    Ensures that tables are created, subscription plans are seeded,
    and the admin user is created only once when the app starts.
    """
    global initialization_done

    if not initialization_done:
        with app.app_context():
            db.create_all()  # Ensure tables are created in the right order
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
def inject_current_app():
    return dict(current_app=current_app)

@app.context_processor
def inject_current_user():
    if current_user.is_authenticated:
        user_type = session.get("user_type")

        if user_type == "driver":
            return dict(js_current_user={
                "id": current_user.id,
                "username": current_user.full_name,  # or email
                "role": "driver"
            })
        elif user_type == "admin":
            return dict(js_current_user={
                "id": current_user.id,
                "username": current_user.username,
                "role": "admin"
            })
        else:  # assume normal user/buyer
            return dict(js_current_user={
                "id": current_user.id,
                "username": getattr(current_user, "username", current_user.email),
                "role": "user"
            })
    return dict(js_current_user=None)

@app.context_processor
def inject_now():
    return {'now': datetime.datetime.utcnow()}

@app.context_processor
def inject_cart_count():
    from flask_login import current_user
    # Only apply cart logic for authenticated buyers
    if current_user.is_authenticated and getattr(current_user, "role", None) == "buyer":
        count = Cart.query.filter_by(user_id=current_user.id).count()
        return {'cart_item_count': count}
    return {'cart_item_count': 0}

@app.route('/db-path', methods=['GET'])
def get_db_path():
    return jsonify({"database_path": DATABASE_URI})

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

#------------COMMUNICATION------------
@app.route('/user/details/<int:user_id>')
@login_required
def user_details(user_id):
    user = User.query.get(user_id)
    if user:
        return jsonify({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "profile_image": user.profile_image,
            "role": user.role
        })
    return jsonify({"error": "User not found"}), 404

#------------STATIC FILES------------
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')


# ------------------------------------------------------
# ROUTE SERVICE INTEGRATION (connect Flask → FastAPI)
# ------------------------------------------------------
ROUTING_SERVICE_URL = "http://localhost:8010/route"  # FastAPI route microservice

def get_route_between_points(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng):
    """
    Sends pickup/dropoff coordinates to the FastAPI routing service
    and returns ETA (minutes), distance (km), and route coordinates.
    """
    try:
        payload = {
            "pickup": {"lat": pickup_lat, "lng": pickup_lng},
            "dropoff": {"lat": dropoff_lat, "lng": dropoff_lng}
        }
        res = requests.post(ROUTING_SERVICE_URL, json=payload, timeout=30)
        if res.status_code == 200:
            return res.json()
        else:
            return {"error": f"Routing API returned {res.status_code}", "details": res.text}
    except Exception as e:
        return {"error": "Failed to connect to routing service", "details": str(e)}


#------------RUN APP------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        initialize_database()

    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
