from datetime import datetime
from decimal import Decimal
from re import template
from fastapi import Depends, Query, Request
from fastapi.responses import RedirectResponse
from flask import app, render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from itsdangerous import URLSafeTimedSerializer
from flask_mail import Message as MailMessage
from extensions import mail
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash
import pandas as pd
from forms import SignupForm, LoginForm, EditProfileForm, ProductForm, AddToCartForm, SettingsForm, SignupCompleteForm
from models import CommissionSettings, User, Buyer, Seller, Product, Cart, ProductComponent, ProductImage, Affiliate,  Admin, AffiliateSignup, Conversation, Message, Category
from database import db
from functools import wraps
from fpdf import FPDF
import os
import requests
import json
import logging
import uuid


routes = Blueprint('routes', __name__, static_folder='static', static_url_path='/static')

#----------------FUNCTIONS DECORATOR-------------------
# Custom decorator for role-based access control
def role_required(required_role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("You need to log in to access this page.", "warning")
                return redirect(url_for('routes.login', next=request.url))
            if current_user.role != required_role:
                flash("You do not have the required role to access this page.", "error")
                return redirect(url_for('routes.access_denied'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@routes.route('/access_denied')
def access_denied():
    return render_template('access_denied.html', title='Access Denied')

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@routes.route('/products', methods=['GET'])
def products():
    # Retrieve query parameters for filtering and sorting
    category = request.args.get('category')
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    sort_by = request.args.get('sort_by', default='date', type=str)  # Options: 'price', 'date'
    order = request.args.get('order', default='desc', type=str)  # Options: 'asc', 'desc'

    # Base query for products
    query = Product.query

    # Apply filters
    if category:
        query = query.filter(Product.category == category)
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)

    # Apply sorting
    if sort_by == 'price':
        if order == 'asc':
            query = query.order_by(Product.price.asc())
        else:
            query = query.order_by(Product.price.desc())
    elif sort_by == 'date':
        if order == 'asc':
            query = query.order_by(Product.date_added.asc())
        else:
            query = query.order_by(Product.date_added.desc())

    # Execute query and fetch results
    products = query.all()

    # Render the template with filtered and sorted products
    return render_template('products.html', products=products)



#---------------------------------------------

# Define the folder for saving receipts
RECEIPTS_FOLDER = 'receipts'
if not os.path.exists(RECEIPTS_FOLDER):
    os.makedirs(RECEIPTS_FOLDER)

def generate_receipt(cart_items, total_amount):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)    
    pdf.cell(200, 10, txt="Receipt", ln=True, align='C')
    pdf.cell(200, 10, txt="Products", ln=True)
    for item in cart_items:
        pdf.cell(200, 10, txt=f"Product: {item['name']}, Quantity: {item['quantity']}, Price: {item['price']}", ln=True)
    pdf.cell(200, 10, txt=f"Total Amount: {total_amount}", ln=True)    
    receipt_file = os.path.join(RECEIPTS_FOLDER, 'receipt.pdf')
    pdf.output(receipt_file)    
    return receipt_file

def send_receipt_to_admin(receipt_file):
    # Placeholder for sending email logic
    # Example: send_email(to='admin@example.com', subject='New Receipt', body='A new receipt has been generated.', attachment=receipt_file)
    pass

#Homepage
@routes.route('/')
def index():
    trending_products = Product.query.order_by(Product.view_count.desc()).limit(20).all()

    static_users_dir = os.path.join(current_app.root_path, 'static', 'users')
    featured_images, featured_videos = [], []

    sellers = User.query.filter_by(role='seller').all()
    current_app.logger.debug("Found %d seller accounts", len(sellers))

    for seller in sellers:
        username = getattr(seller, 'username', None)
        if not username:
            continue

        # Find matching folder (case-insensitive partial match)
        matched_folder = None
        if os.path.isdir(static_users_dir):
            for folder in os.listdir(static_users_dir):
                if username.replace(" ", "_").lower() in folder.lower():
                    matched_folder = folder
                    break

        if not matched_folder:
            current_app.logger.debug("No folder matched for seller: %s", username)
            continue

        # Construct paths
        img_dir = os.path.join(static_users_dir, matched_folder, 'product_images')
        vid_dir = os.path.join(static_users_dir, matched_folder, 'product_videos')

        # Collect images
        if os.path.isdir(img_dir):
            for file in os.listdir(img_dir):
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                    featured_images.append(f'users/{matched_folder}/product_images/{file}')

        # Collect videos
        if os.path.isdir(vid_dir):
            for file in os.listdir(vid_dir):
                if file.lower().endswith(('.mp4', '.mov', '.avi', '.webm', '.mkv')):
                    featured_videos.append(f'users/{matched_folder}/product_videos/{file}')

        current_app.logger.debug(f"Matched folder for {username}: {matched_folder}")

    current_app.logger.debug("Total featured_images: %d", len(featured_images))
    current_app.logger.debug("Total featured_videos: %d", len(featured_videos))

    return render_template(
        'index.html',
        trending_products=trending_products,
        featured_images=featured_images,
        featured_videos=featured_videos
    )

@routes.route('/signup', methods=['GET', 'POST'])
def signup():
    form = SignupForm()
    
    # Determine affiliate code (from query string or form)
    affiliate_code = request.args.get('ref') or (form.affiliate_code.data if form.affiliate_code.data else None)
    referred_affiliate = None

    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip()
        country_code = form.country_code.data.strip()
        phone_number = form.phone_number.data.strip()
        password = form.password.data
        role = form.role.data
        country = form.country.data.strip()
        location = form.location.data.strip()
        lat = form.latitude.data
        lng = form.longitude.data

        # Check for existing email
        if User.query.filter_by(email=email).first():
            flash('Email already exists. Please use a different email.', 'error')
            return redirect(url_for('routes.signup'))

        # Enforce location selection for sellers
        if role == 'seller' and (not lat or not lng):
            flash('Please select a valid location from the suggestions.', 'error')
            return redirect(url_for('routes.signup'))

        try:
            # Create the base User
            new_user = User(
                username=username,
                email=email,
                country_code=country_code,
                phone_number=phone_number,
                country=country,
                location=location,
                role=role,
                signup_complete=False,
                date_joined=datetime.utcnow()
            )
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.flush()  # Ensure new_user.id exists before role-specific records

            # Role-specific creation
            if role == 'buyer':
                db.session.add(Buyer(user_id=new_user.id, username=username, email=email))
            elif role == 'seller':
                db.session.add(Seller(
                    user_id=new_user.id,
                    username=username,
                    email=email,
                    location=location,
                    lat=float(lat),
                    lng=float(lng)
                ))
            elif role == 'affiliate':
                referral_code = str(uuid.uuid4().hex[:8])
                db.session.add(Affiliate(
                    user_id=new_user.id,
                    username=username,
                    email=email,
                    referral_code=referral_code
                ))

            # Handle affiliate referral
            if affiliate_code:
                referred_affiliate = Affiliate.query.filter_by(referral_code=affiliate_code).first()
                if referred_affiliate and referred_affiliate.user_id != new_user.id:
                    db.session.add(AffiliateSignup(
                        affiliate_id=referred_affiliate.id,
                        user_id=new_user.id,
                        timestamp=datetime.utcnow()
                    ))

            db.session.commit()
            login_user(new_user)
            flash('Signup successful. Please complete your profile.', 'success')
            return redirect(url_for('routes.signup_complete'))

        except Exception as e:
            db.session.rollback()
            logging.exception("Error during signup: %s", e)
            flash('An error occurred during signup. Please try again later.', 'error')
            return redirect(url_for('routes.signup'))

    # GET request or form validation failure
    return render_template('signup.html', form=form)

# Replace the signup_complete POST handling with this code (inside routes/signup-complete)
@routes.route('/signup-complete', methods=['GET', 'POST'])
@login_required
def signup_complete():
    form = SignupCompleteForm()
    if current_user.role == 'admin':
        flash('Admin login successful.', 'success')
        return redirect(url_for('admin_routes.admin_dashboard'))

    if form.validate_on_submit():
        id_type = form.id_type.data
        id_front = request.files.get('id_front') or form.id_front.data
        id_back = request.files.get('id_back') or form.id_back.data

        if current_user.role in ['seller', 'affiliate'] and (not id_front or id_front.filename == '' or not id_back or id_back.filename == ''):
            flash('Both front and back ID card images are required for sellers and affiliates.', 'error')
            return redirect(url_for('routes.signup_complete'))

        try:
            # Save ID front
            if id_front and id_front.filename:
                if not allowed_file(id_front.filename):
                    flash('Invalid front ID image format.', 'error')
                    return redirect(url_for('routes.signup_complete'))
                ext = os.path.splitext(secure_filename(id_front.filename))[1] or '.png'
                front_filename = secure_filename(f"{current_user.username}_id_front{ext}")
                front_path = os.path.join(current_app.config['ID_IMAGES_FOLDER'], front_filename)
                os.makedirs(os.path.dirname(front_path), exist_ok=True)
                id_front.save(front_path)
                current_user.id_front_image = front_filename

            # Save ID back
            if id_back and id_back.filename:
                if not allowed_file(id_back.filename):
                    flash('Invalid back ID image format.', 'error')
                    return redirect(url_for('routes.signup_complete'))
                ext = os.path.splitext(secure_filename(id_back.filename))[1] or '.png'
                back_filename = secure_filename(f"{current_user.username}_id_back{ext}")
                back_path = os.path.join(current_app.config['ID_IMAGES_FOLDER'], back_filename)
                os.makedirs(os.path.dirname(back_path), exist_ok=True)
                id_back.save(back_path)
                current_user.id_back_image = back_filename

            current_user.id_type = id_type
            current_user.signup_complete = True
            db.session.commit()

            if current_user.role == 'affiliate':
                affiliate = Affiliate.query.filter_by(user_id=current_user.id).first()
                if not affiliate:
                    new_affiliate = Affiliate(
                        user_id=current_user.id,
                        referral_code=generate_affiliate_code(),
                        username=current_user.username,
                        email=current_user.email
                    )
                    db.session.add(new_affiliate)
                    db.session.commit()

            flash('Profile completed successfully.', 'success')
            if current_user.role == 'seller':
                return redirect(url_for('seller_routes.store_setup', seller_id=current_user.id))
            elif current_user.role == 'affiliate':
                return redirect(url_for('affiliate_routes.affiliate_dashboard'))
            else:
                return redirect(url_for('routes.login'))

        except Exception as e:
            logging.error(f"Error completing profile for user {current_user.username}: {str(e)}")
            flash('An error occurred while completing your profile. Please try again.', 'error')
            return redirect(url_for('routes.signup_complete'))

    return render_template('signup_complete.html', title='Complete Signup', form=form)

import random
import string

def generate_affiliate_code():
    """Generate a unique 8-character affiliate code with uppercase letters and digits."""
    characters = string.ascii_uppercase + string.digits  # Uppercase letters and digits
    while True:
        code = ''.join(random.choices(characters, k=8))  # Generate 8-character code
        existing_affiliate = Affiliate.query.filter_by(referral_code=code).first()
        if not existing_affiliate:  # Ensure it's unique
            return code


@routes.route('/contact-support')
def contact_support():
    return render_template('contact_support.html', title='Contact Support')
#==================================================

@routes.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        # Only normal users (not admin)
        user = User.query.filter_by(email=form.email.data).first()

        if user and user.check_password(form.password.data):
            # Store user type and basic role info BEFORE login_user()
            session['user_type'] = 'user'
            session['role'] = user.role
            session.permanent = True  # respect PERMANENT_SESSION_LIFETIME config

            # Log user in and remember across browser restarts
            login_user(user, remember=form.remember.data)

            # Handle incomplete profile setup
            if not user.signup_complete:
                flash('Please complete your profile before proceeding.', 'warning')
                return redirect(url_for('routes.signup_complete'))

            # Handle first-time seller login
            if user.role == 'seller' and user.is_first_login:
                user.is_first_login = False
                db.session.commit()
                return redirect(url_for('seller_routes.my_store', seller_id=user.id))

            # Role-based redirects
            if user.role == 'buyer':
                next_page = url_for('routes.marketplace')
            elif user.role == 'seller':
                next_page = url_for('seller_routes.seller_dashboard')
            elif user.role == 'affiliate':
                next_page = url_for('affiliate_routes.affiliate_dashboard')
            else:
                next_page = url_for('routes.marketplace')

            # Prepare response and attach user info cookie (non-sensitive)
            user_data = {
                "id": int(user.id),
                "username": user.username,
                "role": user.role
            }

            response = make_response(redirect(next_page))
            response.set_cookie(
                "userDetails",
                json.dumps(user_data, separators=(",", ":"), ensure_ascii=False),
                max_age=7 * 24 * 3600,  # 7 days
                httponly=False,
                path="/"
            )

            flash(f"Welcome back, {user.username}!", "success")
            return response

        flash('Invalid email or password.', 'error')

    return render_template('login.html', title='Login', form=form)

@routes.context_processor
def inject_user_details():
    """Injects user details from cookies into templates automatically."""
    user_cookie = request.cookies.get('userDetails')
    user_details = json.loads(user_cookie) if user_cookie else None
    return dict(user_details=user_details)

@routes.route("/user/details/<int:user_id>")
def get_user_details(user_id):
    """Returns user details in JSON format for external API calls (FastAPI)."""
    user = User.query.get(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "id": user.id,
        "username": user.username,
        "role": user.role
    })

@routes.route('/test_cookie')
def test_cookie():
    user_details = request.cookies.get('userDetails')
    return f"Cookie Value: {user_details}"
#==================================================

def save_profile_image(file, username):
    filename = secure_filename(file.filename)
    user_folder = os.path.join('users', username.replace(' ', '_'), 'profile_images')
    file_path = os.path.join(user_folder, filename)
    # Ensure correct path format for URLs
    file_path = file_path.replace('\\', '/')
    # Ensure the directory exists
    os.makedirs(os.path.join(routes.static_folder, user_folder), exist_ok=True)
    # Save the file
    file.save(os.path.join(routes.static_folder, file_path))
    return file_path


@routes.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()
    countries = [
        {"code": "+213", "iso": "dz"}, {"code": "+244", "iso": "ao"},
        {"code": "+229", "iso": "bj"}, {"code": "+267", "iso": "bw"},
        {"code": "+226", "iso": "bf"}, {"code": "+257", "iso": "bi"},
        {"code": "+238", "iso": "cv"}, {"code": "+237", "iso": "cm"},
        {"code": "+236", "iso": "cf"}, {"code": "+235", "iso": "td"},
        {"code": "+269", "iso": "km"}, {"code": "+242", "iso": "cg"},
        {"code": "+243", "iso": "cd"}, {"code": "+253", "iso": "dj"},
        {"code": "+20", "iso": "eg"}, {"code": "+240", "iso": "gq"},
        {"code": "+291", "iso": "er"}, {"code": "+268", "iso": "sz"},
        {"code": "+251", "iso": "et"}, {"code": "+241", "iso": "ga"},
        {"code": "+220", "iso": "gm"}, {"code": "+233", "iso": "gh"},
        {"code": "+224", "iso": "gn"}, {"code": "+245", "iso": "gw"},
        {"code": "+225", "iso": "ci"}, {"code": "+254", "iso": "ke"},
        {"code": "+266", "iso": "ls"}, {"code": "+231", "iso": "lr"},
        {"code": "+218", "iso": "ly"}, {"code": "+261", "iso": "mg"},
        {"code": "+265", "iso": "mw"}, {"code": "+223", "iso": "ml"},
        {"code": "+222", "iso": "mr"}, {"code": "+230", "iso": "mu"},
        {"code": "+212", "iso": "ma"}, {"code": "+258", "iso": "mz"},
        {"code": "+264", "iso": "na"}, {"code": "+227", "iso": "ne"},
        {"code": "+234", "iso": "ng"}, {"code": "+250", "iso": "rw"},
        {"code": "+239", "iso": "st"}, {"code": "+221", "iso": "sn"},
        {"code": "+248", "iso": "sc"}, {"code": "+232", "iso": "sl"},
        {"code": "+252", "iso": "so"}, {"code": "+27", "iso": "za"},
        {"code": "+211", "iso": "ss"}, {"code": "+249", "iso": "sd"},
        {"code": "+228", "iso": "tg"}, {"code": "+216", "iso": "tn"},
        {"code": "+256", "iso": "ug"}, {"code": "+260", "iso": "zm"},
        {"code": "+263", "iso": "zw"}
    ]

    if form.validate_on_submit():
        current_user.username = form.username.data
        current_user.email = form.email.data
        current_user.country_code = form.country_code.data  # Save country code
        current_user.phone_number = form.phone_number.data
        current_user.country = form.country.data

        # Handle profile image upload
        if form.profile_image.data:
            user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'profile_images')
            os.makedirs(user_folder, exist_ok=True)
            profile_image = form.profile_image.data
            filename = secure_filename(profile_image.filename)
            profile_image_path = os.path.join(user_folder, filename)
            profile_image.save(profile_image_path)
            current_user.profile_image = os.path.normpath(os.path.join(f"users/{current_user.username}_{current_user.id}/profile_images", filename))
        
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('routes.edit_profile'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.country_code.data = current_user.country_code  # Populate country code
        form.phone_number.data = current_user.phone_number
        form.country.data = current_user.country

    return render_template('buyer/edit_profile.html', title='Edit Profile', form=form, countries=countries)

#-----------------------------------------------
@routes.route('/marketplace')
@login_required
def marketplace():
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search_query', '')
    category_filter = request.args.get('category_filter', '')
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    location_filter = request.args.get('location', '')
    sort_option = request.args.get('sort', 'relevance')

    # Fetch unique categories and locations for filters
    categories = db.session.query(Category).all()
    locations = db.session.query(Product.location).distinct().all()

    # Initial product query
    query = Product.query.filter_by(status='Approved')

    # Search query
    if search_query:
        search_pattern = f'%{search_query}%'
        query = query.filter(
            (Product.name.ilike(search_pattern)) | (Product.description.ilike(search_pattern))
        )

    # Category filter
    if category_filter:
        try:
            category_id = int(category_filter)
            query = query.filter(Product.category_id == category_id)
        except ValueError:
            query = query.join(Product.category).filter(Category.name.ilike(f'%{category_filter}%'))

    # Price range
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)

    # Location filter
    if location_filter:
        query = query.filter(Product.location.ilike(f'%{location_filter}%'))

    # Sorting options
    if sort_option == 'price_asc':
        query = query.order_by(Product.price.asc())
    elif sort_option == 'price_desc':
        query = query.order_by(Product.price.desc())
    elif sort_option == 'newest':
        query = query.order_by(Product.date_added.desc())
    elif sort_option == 'condition':
        query = query.order_by(Product.condition.asc())
    elif sort_option == 'brand':
        query = query.order_by(Product.brand.asc())
    else:
        query = query.order_by(Product.date_added.desc())

    # Pagination
    products = query.paginate(page=page, per_page=10)

    return render_template(
        'marketplace.html',
        products=products.items,
        pagination=products,
        search_query=search_query,
        category_filter=category_filter,
        location_filter=location_filter,
        sort_option=sort_option,
        categories=categories,
        locations=locations
    )

@routes.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(routes.static_folder, filename)

@routes.route('/order_confirmation')
@login_required
def order_confirmation():
    # Retrieve the stored session details
    name = session.get('name')
    address = session.get('address')
    payment = session.get('payment')
    cart_items = session.get('cart_items', [])
    total_amount = session.get('total_amount', 0.0)    
    # Clear the cart after confirming the order
    Cart.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return render_template('order_confirmation.html', name=name, address=address, payment=payment, cart_items=cart_items, total_amount=total_amount)


#-------------------------------------------
@routes.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    form = SettingsForm()
    if form.validate_on_submit():
        theme = form.theme.data
        language = form.language.data
        current_user.set_theme(theme)
        current_user.set_language(language)
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('routes.settings'))
    return render_template('settings.html', title='Settings', form=form)


from flask_babel import get_locale

@routes.route('/change_language', methods=['POST'])
@login_required
def change_language():
    language = request.form.get('language')
    if language in ['en', 'fr', 'pt', 'it', 'es']:
        current_user.set_language(language)
        flash('Language updated successfully!', 'success')
    else:
        flash('Invalid language selected.', 'danger')

    # Set the session language
    session['lang'] = language  # Store the language choice in the session

    return redirect(url_for('routes.settings'))


#------------------- PASSWORD RESET -------------------
# Serializer for generating tokens
def get_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

# Forgot Password - Request
@routes.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    serializer = get_serializer()   # ✅ create inside context
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            token = serializer.dumps(user.email, salt='password-reset-salt')
            reset_url = url_for('routes.reset_password', token=token, _external=True)

            msg = MailMessage(
                subject="Password Reset Request",
                sender="noreply@yourapp.com",   # replace with your real email if using Gmail/SMTP
                recipients=[user.email]         # must be a list
            )
            msg.body = f"Hi, click the link to reset your password: {reset_url}"
            mail.send(msg)

            flash('Password reset link sent to your email.', 'info')
            return redirect(url_for('routes.login'))
        else:
            flash('Email not found.', 'danger')

    return render_template('forgot_password.html')

# Reset Password - Form
@routes.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = get_serializer().loads(token, salt='password-reset-salt', max_age=3600)  # valid for 1h
    except:
        flash('The reset link is invalid or expired.', 'danger')
        return redirect(url_for('routes.forgot_password'))

    user = User.query.filter_by(email=email).first_or_404()

    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
        else:
            user.password_hash = generate_password_hash(password)
            db.session.commit()
            flash('Password has been reset! Please login.', 'success')
            return redirect(url_for('routes.login'))

    return render_template('reset_password.html', token=token)

######################
@routes.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('user_type', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('routes.index'))
