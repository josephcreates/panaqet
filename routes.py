from flask import render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from werkzeug.utils import secure_filename
import pandas as pd
from forms import SignupForm, LoginForm, EditProfileForm, ProductForm, AddToCartForm, SettingsForm
from models import User, Buyer, Seller, Product, Cart, ProductComponent, ProductImage, Affiliate,  Admin, AffiliateSignup
from database import db
from functools import wraps
from fpdf import FPDF
import os
import logging
import uuid
import datetime

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
    # Fetch top 20 trending products based on view count
    trending_products = Product.query.order_by(Product.view_count.desc()).limit(20).all()
    return render_template('index.html', trending_products=trending_products)

@routes.route('/signup', methods=['GET', 'POST'])
def signup():
    form = SignupForm()
    affiliate_code = request.args.get('ref')  # Get affiliate code from URL (if exists)
    
    if form.validate_on_submit():
        username = form.username.data
        email = form.email.data
        country_code = form.country_code.data
        phone_number = form.phone_number.data
        password = form.password.data
        role = form.role.data
        country = form.country.data

        # Check if the user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already exists. Please use a different email.', 'error')
            return redirect(url_for('routes.signup'))

        try:
            # Create a new user
            new_user = User(
                username=username,
                email=email,
                country_code=country_code,
                phone_number=phone_number,
                country=country,
                role=role,
                signup_complete=False  # Set to False initially
            )
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()

            # Populate Buyer, Seller, or Affiliate tables based on role
            if role == 'buyer':
                new_buyer = Buyer(
                    user_id=new_user.id,
                    username=username,
                    email=email
                )
                db.session.add(new_buyer)
                db.session.commit()
            elif role == 'seller':
                new_seller = Seller(
                    user_id=new_user.id,
                    username=username,
                    email=email
                )
                db.session.add(new_seller)
                db.session.commit()
            elif role == 'affiliate':
                new_affiliate = Affiliate(
                    user_id=new_user.id,
                    username=username,  # Add username
                    email=email,        # Add email
                    referral_code=str(uuid.uuid4().hex[:8])  # Generate a unique referral code
                )
                db.session.add(new_affiliate)
                db.session.commit()

            # Track affiliate signup if affiliate_code exists
            if affiliate_code:
                affiliate = Affiliate.query.filter_by(referral_code=affiliate_code).first()
                if affiliate:
                    new_signup = AffiliateSignup(affiliate_id=affiliate.id, user_id=new_user.id)
                    db.session.add(new_signup)
                    db.session.commit()

            # Log in the user automatically
            login_user(new_user)
            flash('Signup successful. Please complete your profile.', 'success')
            return redirect(url_for('routes.signup_complete'))  # Redirect to complete signup
        except Exception as e:
            flash('An error occurred while signing up. Please try again later.', 'error')
            logging.error(f"Error during signup: {str(e)}")
            return redirect(url_for('routes.signup_complete'))

    return render_template('signup.html', form=form)

@routes.route('/signup-complete', methods=['GET', 'POST'])
@login_required
def signup_complete():
    if current_user.role == 'admin':
        flash('Admin login successful.', 'success')
        return redirect(url_for('admin_routes.admin_dashboard'))

    if request.method == 'POST':
        id_type = request.form.get('id_type')
        id_front = request.files.get('id_front')
        id_back = request.files.get('id_back')

        if not id_type:
            flash('ID type is required.', 'error')
            return redirect(url_for('routes.signup_complete'))

        if current_user.role in ['seller', 'affiliate'] and (not id_front or not id_back):
            flash('Both front and back ID card images are required for sellers and affiliates.', 'error')
            return redirect(url_for('routes.signup_complete'))

        try:
            # Save ID images
            if id_front:
                front_filename = f"{current_user.username}_id_front.png"
                id_front.save(os.path.join(current_app.config['ID_IMAGES_FOLDER'], front_filename))
                current_user.id_front_image = front_filename

            if id_back:
                back_filename = f"{current_user.username}_id_back.png"
                id_back.save(os.path.join(current_app.config['ID_IMAGES_FOLDER'], back_filename))
                current_user.id_back_image = back_filename

            current_user.id_type = id_type
            current_user.signup_complete = True
            db.session.commit()

            # Generate and assign affiliate code if the user is an affiliate
            if current_user.role == 'affiliate':
                affiliate = Affiliate.query.filter_by(user_id=current_user.id).first()
                if not affiliate:
                    new_affiliate = Affiliate(
                        user_id=current_user.id,
                        referral_code=generate_affiliate_code(),  # Assign unique referral code
                        username=current_user.username,
                        email=current_user.email
                    )
                    db.session.add(new_affiliate)
                    db.session.commit()

            flash('Profile completed successfully.', 'success')

            if current_user.role == 'seller':
                return redirect(url_for('seller_routes.store', seller_id=current_user.id))
            elif current_user.role == 'affiliate':
                return redirect(url_for('affiliate_routes.affiliate_dashboard'))
            else:
                return redirect(url_for('routes.login'))

        except Exception as e:
            logging.error(f"Error completing profile for user {current_user.username}: {str(e)}")
            flash(f'An error occurred while completing your profile. Please try again. Error: {str(e)}', 'error')
            return redirect(url_for('routes.signup_complete'))

    return render_template('signup_complete.html', title='Complete Signup')

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


#@routes.route('/login', methods=['GET', 'POST'])
#def login():
 #   form = LoginForm()
  #  if form.validate_on_submit():
        # Check if the user is in the Admin table first
   #     admin = Admin.query.filter_by(email=form.email.data).first()
    #    if admin and admin.check_password(form.password.data):
     #      flash('Admin login successful.', 'success')
      #      return redirect(url_for('admin_routes.admin_dashboard'))  # Admin redirect

        # If not found in Admin, check in the User table
       # user = User.query.filter_by(email=form.email.data).first()
        #if user and user.check_password(form.password.data):
#            login_user(user)

            # Admin login bypass (already handled above, but keeping this for normal users)
 #           if user.role == 'admin':
  #              flash('Admin login successful.', 'success')
   #             return redirect(url_for('admin_routes.admin_dashboard'))

    #           flash('Please complete your profile before proceeding.', 'warning')
     #           return redirect(url_for('routes.signup_complete'))

            # First-time login redirect for sellers
      #      if user.role == 'seller' and user.is_first_login:
       #         user.is_first_login = False
        #        return redirect(url_for('seller_routes.store', seller_id=user.id))

            # Default redirects
         #   next_page = request.args.get('next')
          #  if not next_page or not next_page.startswith('/'):
           #     if user.role == 'buyer':
            #        next_page = url_for('routes.marketplace')
             #   elif user.role == 'seller':
              #      next_page = url_for('seller_routes.seller_dashboard')
               # elif user.role == 'affiliate':  # Add this condition
                #    next_page = url_for('affiliate_routes.affiliate_dashboard')
#                else:
 #                   next_page = url_for('routes.marketplace')  # Default fallback

  #          return redirect(next_page or url_for('routes.marketplace'))
   #     else:
    #        flash('Invalid email or password.', 'error')

#    return render_template('login.html', title='Login', form=form)


@routes.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        admin = Admin.query.filter_by(email=form.email.data).first()
        if admin and admin.check_password(form.password.data):
            login_user(admin)
            session["user_type"] = "admin"  # Store session type for admin
            flash('Admin login successful.', 'success')
            return redirect(url_for('admin_routes.admin_dashboard'))

        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            session["user_type"] = "user"  # Store session type for normal user
            flash('Login successful.', 'success')

            if not user.signup_complete:
                flash('Please complete your profile before proceeding.', 'warning')
                return redirect(url_for('routes.signup_complete'))

            # First-time login redirect for sellers
            if user.role == 'seller' and user.is_first_login:
                user.is_first_login = False
                db.session.commit()
                return redirect(url_for('seller_routes.store', seller_id=user.id))
            
            # Redirect based on role
            next_page = request.args.get('next')
            if not next_page or not next_page.startswith('/'):
                if user.role == 'buyer':
                    next_page = url_for('routes.marketplace')
                elif user.role == 'seller':
                    next_page = url_for('seller_routes.seller_dashboard')
                elif user.role == 'affiliate':
                    next_page = url_for('affiliate_routes.affiliate_dashboard')
                else:
                    next_page = url_for('routes.marketplace')  # Default fallback
            
            return redirect(next_page or url_for('routes.marketplace'))

        flash('Invalid email or password.', 'error')

    return render_template('login.html', title='Login', form=form)

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

    return render_template('edit_profile.html', title='Edit Profile', form=form, countries=countries)

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

    # Fetch unique categories and locations for filter
    categories = db.session.query(Product.category).distinct().all()
    locations = db.session.query(Product.location).distinct().all()

    # Initial product query, filtering by status
    query = Product.query.filter_by(status='Approved')

    # Search query
    if search_query:
        search_pattern = f'%{search_query}%'
        query = query.filter(
            (Product.name.ilike(search_pattern)) | (Product.description.ilike(search_pattern))
        )

    # Category filter
    if category_filter:
        query = query.filter_by(category=category_filter)

    # Price range filter
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
        query = query.order_by(Product.condition.asc())  # Assuming a 'condition' column exists
    elif sort_option == 'brand':
        query = query.order_by(Product.brand.asc())  # Assuming a 'brand' column exists
    else:
        query = query.order_by(Product.date_added.desc())  # Default sorting by relevance (newest)

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

@routes.route('/change_theme', methods=['POST'])
@login_required
def change_theme():
    theme = request.form.get('theme')
    current_user.set_theme(theme)
    if theme:
        current_user.set_theme(theme)
        flash('Theme updated successfully!', 'success')
    else:
        flash('Invalid theme selected.', 'danger')
    return redirect(url_for('routes.settings'))

@routes.route('/set_theme/<theme>', methods=['POST'])
@login_required
def set_theme(theme):
    if theme not in ['default', 'light', 'dark', 'blue', 'green', 'red']:
        flash('Invalid theme.', 'danger')
        return redirect(url_for('routes.index'))    
    current_user.set_theme(theme)
    flash('Theme updated successfully!', 'success')
    return redirect(request.referrer or url_for('routes.index'))

######################
@routes.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('routes.index'))