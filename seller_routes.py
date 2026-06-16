from flask import render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from forms import SignupForm, LoginForm, EditProfileForm, ProductForm, AddToCartForm, SettingsForm, StoreSetupForm, CommissionPlanForm
from models import User, Seller, Product, Cart, ProductComponent, ProductImage, Order, Buyer, OrderItem, Subscription, SellerSubscription, CommissionPlan
from database import db
import pandas as pd
from datetime import datetime, timedelta
import logging
from io import BytesIO
from functools import wraps
import os

seller_bp = Blueprint('seller_routes', __name__, static_folder='static', static_url_path='/static')

#----------------FUNCTIONS DECORATOR-------------------
# Custom decorator for role-based access control
def role_required(*roles):
    def wrapper(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            if current_user.role not in roles:
                flash('Access denied. You do not have permission to access this page.', 'danger')
                return redirect(url_for('routes.index'))
            return func(*args, **kwargs)
        return decorated_view
    return wrapper

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


#-------SELLER---------
# Seller Dashboard: Where sellers can manage their products and commissions.
@seller_bp.route('/seller_dashboard', methods=['GET', 'POST'])
@login_required
def seller_dashboard():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    form = ProductForm()
    if form.validate_on_submit():
        # Create new product
        product = Product(
            name=form.name.data,
            description=form.description.data,
            price=form.price.data,
            category=form.category.data,
            condition=form.condition.data,
            location=form.location.data,
            seller_id=current_user.id,
            status='Pending',
            is_package=form.is_package.data
        )
        db.session.add(product)
        db.session.commit()

        # Handle image upload
        if form.image.data:
            image_file = form.image.data
            if allowed_file(image_file.filename):
                user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'product_images')
                os.makedirs(user_folder, exist_ok=True)
                filename = secure_filename(image_file.filename)
                image_path = os.path.join(user_folder, filename)
                image_file.save(image_path)

                image_url = f"users/{current_user.username}_{current_user.id}/product_images/{filename}"
                product_image = ProductImage(product_id=product.id, image_url=image_url)
                db.session.add(product_image)
                db.session.commit()
            else:
                flash('Invalid file type. Please upload an image.', 'error')
                return redirect(url_for('seller_bp.seller_dashboard'))

        # Handle package product components
        if form.is_package.data:
            for option in form.package_options.data:
                component = ProductComponent(
                    product_id=product.id,
                    name=option['name'],
                    price=option['price']
                )
                db.session.add(component)
                if option['image']:
                    component_image_file = option['image']
                    if allowed_file(component_image_file.filename):
                        component_folder = os.path.join(user_folder, 'components')
                        os.makedirs(component_folder, exist_ok=True)
                        filename = secure_filename(component_image_file.filename)
                        component_image_path = os.path.join(component_folder, filename)
                        component_image_file.save(component_image_path)
                        component.image_url = os.path.join(f"users/{current_user.username}_{current_user.id}/product_images/components", filename)
                db.session.commit()

        flash('Product added successfully. It is pending admin approval.', 'success')
        return redirect(url_for('seller_bp.seller_dashboard'))

    # Fetch approved products for pagination
    page = request.args.get('page', 1, type=int)
    
    approved_products = Product.query.filter_by(
        seller_id=current_user.id, status='Approved'
    ).options(joinedload(Product.commission_plans)).paginate(page=page, per_page=10)


    # Fetch orders for the seller by checking the product's seller_id through OrderItem
    orders = db.session.query(Order, Product).join(OrderItem, OrderItem.order_id == Order.id)\
        .join(Product, Product.id == OrderItem.product_id).all()

    # Fetch statistics data
    total_products = Product.query.filter_by(seller_id=current_user.id).count()
    total_sales = db.session.query(db.func.sum(Product.price)).filter(Product.seller_id == current_user.id).scalar() or 0
    
    return render_template(
        'seller_dashboard.html', 
        form=form,
        approved_products=approved_products,
        total_products=total_products,
        total_sales=total_sales,
        orders=orders,
        conversation=None)


# Commission Plan Management: Sellers can create commission plans.
@seller_bp.route('/commission_plans', methods=['GET', 'POST'])
@login_required
def commission_plans():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    form = CommissionPlanForm()
    if form.validate_on_submit():
        commission_plan = CommissionPlan(
            plan_name=form.plan_name.data,
            commission_rate=form.commission_rate.data,
            description=form.description.data,
            seller_id=current_user.id
        )
        db.session.add(commission_plan)
        db.session.commit()
        flash('Commission plan created successfully!', 'success')
        return redirect(url_for('seller_routes.commission_plans'))

    # Fetch existing commission plans and associated products
    commission_plans = CommissionPlan.query.filter_by(seller_id=current_user.id).all()
    products = Product.query.filter_by(seller_id=current_user.id).all()  # Adjust as needed

    return render_template(
        'commission_plans.html',
        form=form,
        commission_plans=commission_plans,
        products=products
    )

@seller_bp.route('/attach_commission_plan', methods=['POST'])
@login_required
def attach_commission_plan():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    # Get form data
    plan_id = request.form.get('plan_id')
    product_ids = request.form.getlist('product_ids')

    # Fetch the commission plan
    commission_plan = CommissionPlan.query.filter_by(id=plan_id, seller_id=current_user.id).first()
    if not commission_plan:
        flash('Invalid commission plan.', 'danger')
        return redirect(url_for('seller_routes.commission_plans'))

    # Attach the commission plan to selected products and calculate commission for each
    products = Product.query.filter(Product.id.in_(product_ids), Product.seller_id == current_user.id).all()
    for product in products:
        if product not in commission_plan.products:
            commission_plan.products.append(product)
            
            # Calculate the commission for each product based on its price and commission rate
            commission_amount = (commission_plan.commission_rate / 100) * product.price
            product.commission = commission_amount  # Store calculated commission

    db.session.commit()
    flash('Commission plan attached to selected products successfully!', 'success')
    return redirect(url_for('seller_routes.commission_plans'))

@seller_bp.route('/approve_order/<int:order_id>', methods=['POST'])
@login_required
def approve_order(order_id):
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))
    order = Order.query.get_or_404(order_id)
    order.status = 'Approved'
    db.session.commit()
    flash('Order approved successfully.', 'success')
    return jsonify({"message": f"Order {order_id} approved"})

@seller_bp.route('/decline_order/<int:order_id>', methods=['POST'])
@login_required
def decline_order(order_id):
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))
    order = Order.query.get_or_404(order_id)
    order.status = 'Declined'
    db.session.commit()
    flash('Order declined successfully.', 'success')
    return redirect(url_for('seller_bp.seller_dashboard'))

@seller_bp.route('/store-setup', methods=['GET', 'POST'])
@login_required
def store_setup():
    form = StoreSetupForm()  # Instantiate the form

    if form.validate_on_submit():
        store_name = form.store_name.data
        store_description = form.store_description.data
        store_logo = form.store_logo.data

        # Validation checks
        if not store_logo:
            flash('Store logo is required.', 'error')
            return redirect(url_for('seller_routes.store_setup'))

        # Check file size
        store_logo.seek(0, os.SEEK_END)
        file_size = store_logo.tell()
        store_logo.seek(0)
        if file_size > current_app.config['MAX_CONTENT_LENGTH']:
            flash('File size exceeds the limit of 500MB', 'error')
            return redirect(url_for('seller_routes.store_setup'))

        # Check allowed file types
        def allowed_file(filename):
            return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

        if not allowed_file(store_logo.filename):
            flash('Invalid file type. Allowed types: png, jpg, jpeg, gif, mp4, avi, mov.', 'error')
            return redirect(url_for('seller_routes.store_setup'))

        try:
            # Save the logo
            logo_filename = f"{current_user.username}_store_logo.png"
            logo_path = os.path.join(current_app.config['STORE_LOGOS_FOLDER'], logo_filename)
            store_logo.save(logo_path)

            # Save details to database
            current_user.store_name = store_name
            current_user.store_description = store_description
            current_user.store_logo = logo_filename
            db.session.commit()

            flash('Store setup saved successfully!', 'success')
            return redirect(url_for('seller_routes.seller_dashboard'))
        except Exception as e:
            flash(f'Error while saving store: {str(e)}', 'error')
            return redirect(url_for('seller_routes.store', seller_id=current_user.id))  # Pass seller_id

    return render_template('store_setup.html', form=form)  # Pass the form to the template

@seller_bp.route('/store-page/<int:seller_id>', methods=['GET'])
@login_required
def store(seller_id):
    # Fetch the seller based on the provided seller_id
    seller = User.query.filter_by(id=seller_id, role='seller').first()

    if not seller:
        flash('Seller not found.', 'error')
        return redirect(url_for('routes.marketplace'))

    # Use profile image as store logo
    store_logo = seller.profile_image  # Use profile image as store logo
    
    # Use a placeholder if store_name is not defined
    store_name = f"{seller.username}'s Store"
    
    # Pagination: Fetch approved products for the seller
    page = request.args.get('page', 1, type=int)
    approved_products = Product.query.filter_by(seller_id=seller.id, status='Approved').paginate(page=page, per_page=10)

    # Render the store page with the seller's details and approved products
    return render_template('store.html', 
                           title=store_name, 
                           seller=seller, 
                           store_logo=store_logo,
                           approved_products=approved_products)

@seller_bp.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()
    if form.validate_on_submit():
        current_user.username = form.username.data
        current_user.email = form.email.data
        current_user.phone_number = form.phone_number.data  # Update phone number

        # Handle profile image upload
        if form.profile_image.data:
            user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'profile_images')
            os.makedirs(user_folder, exist_ok=True)
            profile_image = form.profile_image.data
            filename = secure_filename(profile_image.filename)
            profile_image_path = os.path.join(user_folder, filename)
            profile_image.save(profile_image_path)
            # Update user's profile image path
            current_user.profile_image = os.path.normpath(os.path.join(f"users/{current_user.username}_{current_user.id}/profile_images", filename))
        
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('seller_routes.seller_dashboard'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.phone_number.data = current_user.phone_number  # Populate phone number field

    return render_template('edit_profile.html', title='Edit Profile', form=form)


# Seller Product Creation: Now used only to create and manage products.
@seller_bp.route('/add_product', methods=['GET', 'POST'])
def add_product():
    # Fetch the seller's profile
    seller_profile = Seller.query.filter_by(id=current_user.id).first()
    seller_location = seller_profile.location if seller_profile else ''

    if request.method == 'POST':
        # Extract product data from the form
        name = request.form.get('name')
        description = request.form.get('description')
        price = float(request.form.get('price', 0.0))
        category = request.form.get('category')
        location = request.form.get('location')
        condition = request.form.get('condition')
        brand = request.form.get('brand')
        gender = request.form.get('gender')
        color = request.form.get('color')
        size = request.form.get('size')
        is_package = request.form.get('is_package') == 'on'

        # Create and save the product
        new_product = Product(
            name=name,
            description=description,
            price=price,
            category=category,
            seller_id=current_user.id,
            location=location,
            condition=condition,
            brand=brand,
            gender=gender,
            color=color,
            size=size,
            is_package=is_package,
            status='Pending'
        )
        db.session.add(new_product)
        db.session.commit()  # Save product to get its ID

        # Handle multiple images
        if 'images' in request.files:
            files = request.files.getlist('images')
            user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'product_images')
            os.makedirs(user_folder, exist_ok=True)

            for file in files:
                if file and allowed_file(file.filename):
                    # Save the file
                    filename = secure_filename(file.filename)
                    file_path = os.path.join(user_folder, filename)
                    file.save(file_path)

                    # Save the file reference in the database
                    product_image = ProductImage(product_id=new_product.id, image_url=file_path)
                    db.session.add(product_image)

        db.session.commit()  # Save images
        flash('Product added successfully!', 'success')
        return redirect(url_for('seller_routes.seller_dashboard'))

    # Render form for adding products, passing seller location
    return render_template('seller_dashboard.html', form=ProductForm(), seller_location=seller_location)


#----------------------------------------------------
@seller_bp.route('/product_details/<int:product_id>', methods=['GET', 'POST'])
@login_required
def product_details(product_id):
    product = Product.query.get_or_404(product_id)
    product.increment_view_count()  # Increment view count
    
    form = AddToCartForm()
    
    # Ensure components for packages are loaded
    if product.is_package:
        components = ProductComponent.query.filter_by(product_id=product_id).all()
        form.components.choices = [(component.id, component.name) for component in components]
        form.quantities.choices = [1] * len(components)

    if form.validate_on_submit():
        if product.is_package:
            # Handle adding package components
            for component_id, quantity in zip(form.components.data, form.quantities.data):
                if quantity > 0:
                    cart_item = Cart.query.filter_by(user_id=current_user.id, product_id=product_id, component_id=component_id).first()
                    if cart_item:
                        cart_item.quantity += quantity
                    else:
                        cart_item = Cart(user_id=current_user.id, product_id=product_id, component_id=component_id, quantity=quantity)
                        db.session.add(cart_item)
            db.session.commit()
        else:
            # Handle adding single item
            cart_item = Cart.query.filter_by(user_id=current_user.id, product_id=product_id).first()
            if cart_item:
                cart_item.quantity += form.quantity.data
            else:
                cart_item = Cart(user_id=current_user.id, product_id=product_id, quantity=form.quantity.data)
                db.session.add(cart_item)
            db.session.commit()
        
        flash('Product added to cart!', 'success')
        return redirect(url_for('seller_routes.product_details', product_id=product_id))

    # Pass the first image URL directly if it exists
    image_url = product.images[0].image_url.replace('\\', '/') if product.images else url_for('static', filename='default_image.jpg')
    qr_code_url = url_for('static', filename=f'qr_codes/{product.qr_code}') if product.qr_code else None
    
    return render_template('product_details.html', product=product, form=form, image_url=image_url, qr_code_url=qr_code_url)

@seller_bp.route('/product_images/<path:filename>')
def product_images(filename):
    # Path to the user's product_images subdirectory
    return send_from_directory(os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'product_images'), filename)

@seller_bp.route('/delete_product/<int:product_id>', methods=['GET', 'POST'])
@login_required
@role_required('seller')
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    if product.seller_id != current_user.id:
        abort(403)    
    if request.method == 'POST':
        db.session.delete(product)
        db.session.commit()
        flash('Product deleted successfully!', 'success')
        return redirect(url_for('seller_routes.seller_dashboard'))    
    # If GET request (for confirmation page or modal)
    form = FlaskForm()  # Create an empty form for CSRF token
    return render_template('delete_product.html', product=product, form=form)

####################
@seller_bp.route('/seller/available_subscriptions', methods=['GET'])
@login_required
def available_seller_subscriptions():
    if not current_user.is_seller:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    # Fetch active subscriptions
    active_subscriptions = Subscription.query.filter_by(status='active').all()
    if not active_subscriptions:
        flash("No active subscriptions available.", "warning")

    # Get the seller associated with the current user
    seller = Seller.query.filter_by(user_id=current_user.id).first()

    if not seller:
        user = User.query.get(current_user.id)
        if user:
            seller = Seller(user_id=user.id)
            db.session.add(seller)
            db.session.commit()
            flash("Seller profile was missing and has been created.", "info")
        else:
            flash("User profile not found. Please contact support.", "danger")
            return redirect(url_for('index'))

    # Retrieve all subscriptions the seller is already subscribed to
    subscribed_subscriptions = [
        subscription.subscription
        for subscription in SellerSubscription.query.filter_by(seller_id=seller.id).all()
    ]

    # Filter out the active subscriptions that the seller has already subscribed to
    unsubscribed_subscriptions = [
        subscription for subscription in active_subscriptions 
        if subscription not in subscribed_subscriptions
    ]

    # Prevent reloading with the same data
    if not unsubscribed_subscriptions:
        flash("You have already subscribed to all available plans.", "info")

    return render_template(
        'available_seller_subscriptions.html', 
        unsubscribed_subscriptions=unsubscribed_subscriptions,  # Show only available subscriptions to subscribe to
        subscribed_subscriptions=subscribed_subscriptions
    )

# Subscribe/Unsubscribe to a subscription
@seller_bp.route('/seller/subscriptions/<int:subscription_id>/toggle', methods=['POST'])
@login_required
def toggle_seller_subscription(subscription_id):
    if not current_user.is_seller:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    seller = Seller.query.filter_by(user_id=current_user.id).first()
    subscription = Subscription.query.get_or_404(subscription_id)

    # Calculate valid_until based on the subscription's validity_period (in days)
    valid_until = datetime.utcnow() + timedelta(days=subscription.validity_period)

    if subscription in seller.subscriptions:
        seller.subscriptions.remove(subscription)
        flash(f"Unsubscribed from {subscription.name}.", "success")
    else:
        # Add the subscription with the calculated valid_until
        seller_subscription = SellerSubscription(
            seller_id=seller.id,
            subscription_id=subscription.id,
            subscribed_on=datetime.utcnow(),
            valid_until=valid_until  # Set valid_until date
        )
        db.session.add(seller_subscription)
        flash(f"Subscribed to {subscription.name}.", "success")

    db.session.commit()
    return redirect(url_for('seller_routes.available_seller_subscriptions'))

@seller_bp.route('/seller/my_subscriptions', methods=['GET'])
@login_required
def my_subscriptions():
    if not current_user.is_seller:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    seller = Seller.query.filter_by(user_id=current_user.id).first()

    # Handle the case where the seller does not exist
    if not seller:
        flash("You are not a seller or your seller profile is missing.", "danger")
        return redirect(url_for('routes.index'))

    # Get all subscriptions for the seller
    subscriptions = Subscription.query.join(SellerSubscription).filter(SellerSubscription.seller_id == seller.id).all()

    # Debugging output
    print(f"Subscriptions for seller {seller.id}: {subscriptions}")

    return render_template('my_subscriptions.html', subscriptions=subscriptions, current_date=datetime.utcnow())

@seller_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    form = SettingsForm()
    if form.validate_on_submit():
        theme = form.theme.data
        current_user.set_theme(theme)
        flash('Theme updated successfully!', 'success')
        return redirect(url_for('seller_routes.settings'))
    return render_template('settings.html', title='Settings', form=form)
