from flask import render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from sqlalchemy.orm import joinedload
from sqlalchemy import func, desc
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from forms import EditProfileForm, ProductForm, AddToCartForm, SettingsForm, StoreSetupForm, CommissionPlanForm, get_category_choices
from models import Category, CommissionSettings, Conversation, ProductVideo, User, Seller, Product, Cart, ProductComponent, ProductImage, Order, Buyer, OrderItem, Subscription, SellerSubscription, CommissionPlan, Referral, Affiliate
from wallet import get_or_create_wallet, debit_wallet
from decimal import Decimal
from database import db
import pandas as pd
from datetime import datetime, timedelta
import logging
from io import BytesIO
from functools import wraps
import os
import requests

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

def allowed_file_ext(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in current_app.config['ALLOWED_EXTENSIONS']


@seller_bp.app_context_processor
def inject_new_orders_count():
    if current_user.is_authenticated:
        # Count distinct orders that include at least one product from this seller
        new_orders_count = (
            db.session.query(func.count(Order.id.distinct()))
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(Product, Product.id == OrderItem.product_id)
            .filter(Product.seller_id == current_user.id, Order.status == 'Pending')
            .scalar()
        )
    else:
        new_orders_count = 0
    return dict(new_orders_count=new_orders_count)
    
#-------SELLER---------
# Seller Dashboard: Where sellers can manage their products and commissions.
@seller_bp.route('/seller-dashboard', methods=['GET', 'POST'])
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

                # Saving main product images (multiple)
                files = request.files.getlist('images')
                for file in files:
                    if file and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        save_path = os.path.join(user_folder, filename)                # filesystem path
                        file.save(save_path)
                        # store relative path (no leading slash, no "static/")
                        relative = f"users/{current_user.username}_{current_user.id}/product_images/{filename}"
                        product_image = ProductImage(product_id=product.id, image_url=relative)
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
    ).options(joinedload(Product.commission_plan)).paginate(page=page, per_page=10)

    # Fetch orders for the seller by checking the product's seller_id through OrderItem
    orders = db.session.query(Order, Product).join(OrderItem, OrderItem.order_id == Order.id)\
        .join(Product, Product.id == OrderItem.product_id).all()

    # Fetch statistics data
    total_products = Product.query.filter_by(seller_id=current_user.id).count()
    total_sales = db.session.query(db.func.sum(Product.price)).filter(Product.seller_id == current_user.id).scalar() or 0
    conversations = Conversation.query.filter_by(seller_id=current_user.id).all()
    
    return render_template(
        'seller/dashboard.html', 
        form=form,
        approved_products=approved_products,
        total_products=total_products,
        total_sales=total_sales,
        orders=orders,
        conversations=conversations,
        active='chat')

@seller_bp.route('/orders')
@login_required
def orders():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    # Fetch all order items that belong to this seller
    rows = (
        db.session.query(Order, OrderItem, Product)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(Product, Product.id == OrderItem.product_id)
        .filter(Product.seller_id == current_user.id)
        .order_by(Order.id.desc())
        .all()
    )

    # Group orders by ID for template consumption
    grouped_orders = {}
    for order, order_item, product in rows:
        if order.id not in grouped_orders:
            grouped_orders[order.id] = {
                'order': order,
                'items': []
            }
        order_item.product = product
        grouped_orders[order.id]['items'].append(order_item)

    # Determine if order_detail route is defined (for Details button)
    details_enabled = any(rule.endpoint == 'seller_routes.order_detail' for rule in current_app.url_map.iter_rules())

    return render_template(
        'seller/orders.html',
        grouped_orders=grouped_orders,
        details_enabled=details_enabled
    )

# Commission Plan Management: Sellers can create commission plans.
@seller_bp.route('/commission-plans', methods=['GET', 'POST'])
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

    return render_template('seller/commission_plans.html',
        form=form,
        commission_plans=commission_plans,
        products=products
    )


@seller_bp.route('/attach-commission-plan', methods=['GET', 'POST'])
@login_required
def attach_commission_plan():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    # in commission_plans view (before allowing POST)
    if admin_control_enabled():
        flash('Admin currently controls commission rates. Creating new commission plans is disabled.', 'warning')
        return redirect(url_for('seller_routes.commission_plans'))

    # POST -> attach
    if request.method == 'POST':
        plan_id = request.form.get('plan_id')
        product_ids = request.form.getlist('product_ids')

        if not plan_id:
            flash('Please select a commission plan.', 'warning')
            return redirect(url_for('seller_routes.attach_commission_plan'))

        commission_plan = CommissionPlan.query.filter_by(id=plan_id, seller_id=current_user.id).first()
        if not commission_plan:
            flash('Invalid commission plan selected.', 'danger')
            return redirect(url_for('seller_routes.attach_commission_plan'))

        if not product_ids:
            flash('Please select at least one product to attach the plan to.', 'warning')
            return redirect(url_for('seller_routes.attach_commission_plan'))

        products = Product.query.filter(Product.id.in_(product_ids), Product.seller_id == current_user.id).all()
        if not products:
            flash('No valid products found to attach.', 'warning')
            return redirect(url_for('seller_routes.attach_commission_plan'))

        for product in products:
            product.commission_plan_id = commission_plan.id
            product.commission = (commission_plan.commission_rate / 100.0) * float(product.price or 0)

            try:
                commission_amount = (commission_plan.commission_rate / 100.0) * float(product.price or 0)
            except Exception:
                commission_amount = 0
            product.commission = commission_amount

        db.session.commit()
        flash('Commission plan attached to selected products successfully!', 'success')

        # Redirect back and include the selected plan so page can pre-select it and reflect changes
        return redirect(url_for('seller_routes.attach_commission_plan', selected_plan=plan_id))

    # In commission_plans view
    if admin_control_enabled():
        flash('Commission creation is disabled while admin manages commission rates.', 'warning')
        return redirect(url_for('seller_routes.seller_dashboard'))
    # GET -> render
    selected_plan = request.args.get('selected_plan', None)
    form = CommissionPlanForm()  # only used for CSRF token (hidden_tag)
    commission_plans = CommissionPlan.query.filter_by(seller_id=current_user.id).all()
    products = Product.query.filter_by(seller_id=current_user.id).all()

    return render_template(
        'seller/attach_commission_plan.html',
        form=form,
        commission_plans=commission_plans,
        products=products,
        selected_plan=selected_plan
    )

# Helper to check if admin control is enabled
def admin_control_enabled():
    s = CommissionSettings.query.order_by(CommissionSettings.id.desc()).first()
    return bool(s and s.admin_control_enabled)


# Helper to compute overall order status based on item statuses
def compute_order_status(order):
    """Return aggregated status for an order based on its items"""
    # Collect distinct item statuses
    statuses = {item.status.lower() for item in order.items}
    if not statuses:
        return 'Pending'
    if statuses == {'approved'}:
        return 'Approved'
    if statuses == {'declined'}:
        return 'Declined'
    # Mixed values: if there's at least one approved and at least one declined/pending
    if 'approved' in statuses and ('pending' in statuses or 'declined' in statuses):
        return 'Partially Approved'
    # If some pending and none approved
    if 'pending' in statuses:
        return 'Pending'
    return 'Processing'

@seller_bp.route('/approve_order/<int:order_id>', methods=['POST'])
@login_required
def approve_order(order_id):
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    order = Order.query.get_or_404(order_id)

    # Find order_items in this order that belong to this seller and are pending
    seller_items = (
        db.session.query(OrderItem)
        .join(Product, Product.id == OrderItem.product_id)
        .filter(OrderItem.order_id == order_id, Product.seller_id == current_user.id, OrderItem.status == 'Pending')
        .all()
    )

    if not seller_items:
        flash('No pending items in this order belong to you (or items already handled).', 'warning')
        return redirect(url_for('seller_routes.orders'))

    # Mark seller's items as Approved
    for oi in seller_items:
        oi.status = 'Approved'

    # Recompute overall order status
    db.session.commit()  # commit item changes before recalculation/read
    db.session.refresh(order)  # refresh to pick up relationships

    order.status = compute_order_status(order)
    db.session.commit()

    flash(f'Order #{order.id}: your items approved. Order status: {order.status}', 'success')
    return redirect(url_for('seller_routes.orders'))

@seller_bp.route('/decline_order/<int:order_id>', methods=['POST'])
@login_required
def decline_order(order_id):
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    order = Order.query.get_or_404(order_id)

    seller_items = (
        db.session.query(OrderItem)
        .join(Product, Product.id == OrderItem.product_id)
        .filter(OrderItem.order_id == order_id, Product.seller_id == current_user.id, OrderItem.status == 'Pending')
        .all()
    )

    if not seller_items:
        flash('No pending items in this order belong to you (or items already handled).', 'warning')
        return redirect(url_for('seller_routes.orders'))

    for oi in seller_items:
        oi.status = 'Declined'

    db.session.commit()
    db.session.refresh(order)
    order.status = compute_order_status(order)
    db.session.commit()

    flash(f'Order #{order.id}: your items declined. Order status: {order.status}', 'warning')
    return redirect(url_for('seller_routes.orders'))

@seller_bp.route("/analytics")
def analytics():
    return render_template("seller/analytics.html")

@seller_bp.route('/store-setup', methods=['GET', 'POST'])
@login_required
def store_setup():
    form = StoreSetupForm()

    # instantiate early — CSRF needs the session cookie
    if form.validate_on_submit():
        store_name = form.store_name.data
        store_description = form.store_description.data
        store_logo = form.store_logo.data  # Werkzeug FileStorage

        if not store_logo:
            flash('Store logo is required.', 'error')
            return redirect(url_for('seller_routes.store_setup'))

        # optional: size check
        store_logo.seek(0, os.SEEK_END)
        file_size = store_logo.tell()
        store_logo.seek(0)
        if file_size > current_app.config.get('MAX_CONTENT_LENGTH', 0):
            flash('File size exceeds the allowed limit.', 'error')
            return redirect(url_for('seller_routes.store_setup'))

        if not allowed_file_ext(store_logo.filename):
            flash('Invalid file type. Allowed: ' + ','.join(current_app.config.get('ALLOWED_EXTENSIONS', [])), 'error')
            return redirect(url_for('seller_routes.store_setup'))

        try:
            filename = secure_filename(store_logo.filename)
            # normalize extension or force png if desired:
            logo_filename = f"{current_user.username}_{current_user.id}_{filename}"
            logo_path = os.path.join(current_app.config['STORE_LOGOS_FOLDER'], logo_filename)
            store_logo.save(logo_path)

            # store *relative* path or filename in DB so url_for('static', ...) works
            current_user.store_logo = f"store_logos/{logo_filename}"
            current_user.store_name = store_name
            current_user.store_description = store_description
            db.session.commit()

            flash('Store setup saved successfully!', 'success')
            return redirect(url_for('seller_routes.my_store'))
        except Exception as e:
            current_app.logger.exception("Error saving store logo")
            flash(f'Error while saving store: {e}', 'error')
            return redirect(url_for('seller_routes.store_setup'))

    # show validation errors (helpful while debugging CSRF)
    if form.errors:
        # don't expose sensitive things in production; this is for debugging
        current_app.logger.debug(f"StoreSetupForm errors: {form.errors}")
        for field, errs in form.errors.items():
            for err in errs:
                flash(f"{field}: {err}", "warning")

    return render_template('seller/store_setup.html', form=form)

@seller_bp.route('/my-store', methods=['GET'])
@login_required
def my_store():
    seller = User.query.filter_by(id=current_user.id, role='seller').first()

    if not seller:
        flash('Seller not found.', 'error')
        return redirect(url_for('routes.marketplace'))

    store_logo = seller.profile_image
    store_name = f"{seller.username}'s Store"

    page = request.args.get('page', 1, type=int)
    approved_products = Product.query.filter_by(
        seller_id=seller.id, status='Approved'
    ).paginate(page=page, per_page=10)

    return render_template('seller/store.html',
                           title=store_name,
                           seller=seller,
                           store_logo=store_logo,
                           approved_products=approved_products)

@seller_bp.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()

    if form.validate_on_submit():
        # Update basic user fields
        current_user.username = form.username.data
        current_user.email = form.email.data
        current_user.phone_number = form.phone_number.data

        # Update country and location using the model method
        current_user.update_location(country=form.country.data, location=form.location.data)

        # Handle profile image upload
        if form.profile_image.data:
            user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'profile_images')
            os.makedirs(user_folder, exist_ok=True)

            profile_image = form.profile_image.data
            filename = secure_filename(profile_image.filename)
            profile_image_path = os.path.join(user_folder, filename)
            profile_image.save(profile_image_path)

            # Update user profile image path
            current_user.profile_image = os.path.normpath(
                os.path.join(f"users/{current_user.username}_{current_user.id}/profile_images", filename)
            )

        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('seller_routes.dashboard'))

    elif request.method == 'GET':
        # Prepopulate form fields with current user data
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.phone_number.data = current_user.phone_number
        form.country.data = current_user.country
        form.location.data = current_user.location

    return render_template('seller/edit_profile.html', title='Edit Profile', form=form)

@seller_bp.route('/location_suggestions')
@login_required
def location_suggestions():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "addressdetails": 1,
        "limit": 5
    }
    response = requests.get(url, params=params)
    results = response.json()
    
    suggestions = []
    for r in results:
        display_name = r.get('display_name')
        lat = r.get('lat')
        lon = r.get('lon')
        suggestions.append({"name": display_name, "lat": lat, "lon": lon})
    
    return jsonify(suggestions)


# Helper to get category choices for dropdown
def get_category_choices():
    """
    Returns list of (id, label) where label includes indentation for hierarchy.
    Example: [(1, 'Electronics'), (2, '  → Laptops'), ...]
    """
    cats = Category.query.order_by(Category.name).all()
    # build parent -> children map
    by_parent = {}
    for c in cats:
        by_parent.setdefault(c.parent_id, []).append(c)

    def walk(parent_id=None, prefix=''):
        out = []
        children = sorted(by_parent.get(parent_id, []), key=lambda x: x.name)
        for child in children:
            out.append((child.id, f"{prefix}{child.name}"))
            out.extend(walk(child.id, prefix + "  → "))
        return out

    return walk(None, '')


# Seller Product Creation: Now used only to create and manage products.
@seller_bp.route('/add_product', methods=['GET', 'POST'])
@login_required
def add_product():
    if current_user.role != 'seller':
        flash('Access denied', 'danger')
        return redirect(url_for('routes.index'))

    seller_profile = Seller.query.filter_by(user_id=current_user.id).first()
    seller_location = seller_profile.location if seller_profile else ''

    # instantiate form and set choices early (so WTForms can validate)
    form = ProductForm()
    form.category.choices = get_category_choices()

    # If client used the custom picker, the selected value will be in request.form['category'] (hidden input).
    # Ensure the WTForm field sees it as an int before validate_on_submit()
    if request.method == 'POST':
        # Debug: show incoming form keys in the console (remove in production)
        current_app.logger.debug("add_product POST form keys: %s", list(request.form.keys()))
        current_app.logger.debug("add_product POST form values (category): %s", request.form.get('category'))

        # If WTForms didn't populate form.category.data (sometimes happens with custom inputs),
        # attempt to set it manually from request.form.
        if not form.category.data:
            cat_raw = request.form.get('category')
            if cat_raw:
                try:
                    form.category.data = int(cat_raw)
                except (ValueError, TypeError):
                    form.category.data = None

    # run validation
    if form.validate_on_submit():
        # server-side extra check: ensure category exists
        cat_id = form.category.data
        category_obj = Category.query.get(cat_id)
        if not category_obj:
            flash('Invalid category selected.', 'danger')
            # log details for debugging
            current_app.logger.warning("add_product: invalid category id submitted: %r", cat_id)
            return redirect(url_for('seller_routes.add_product'))

        # Create Product (same as before)
        new_product = Product(
            name=form.name.data,
            description=form.description.data,
            price=form.price.data,
            category_id=cat_id,
            seller_id=current_user.id,
            location=form.location.data or seller_location,
            condition=form.condition.data,
            brand=form.brand.data,
            gender=form.gender.data,
            color=form.color.data,
            size=form.size.data,
            is_package=form.is_package.data,
            status='Pending'
        )
        db.session.add(new_product)
        db.session.flush()  # get product id before commit

        # Save images (same as you have)
        username_safe = secure_filename(current_user.username)
        user_folder_rel = f"users/{username_safe}_{current_user.id}/product_images"
        user_folder_fs = os.path.join(current_app.static_folder, user_folder_rel)
        os.makedirs(user_folder_fs, exist_ok=True)

        for f in request.files.getlist('images'):
            if f and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                f.save(os.path.join(user_folder_fs, filename))
                img_rel_path = f"{user_folder_rel}/{filename}"
                db.session.add(ProductImage(product_id=new_product.id, image_url=img_rel_path))

        # Save videos (allow multiple)
        video_files = request.files.getlist('videos')
        if video_files:
            user_video_folder_rel = f"users/{username_safe}_{current_user.id}/product_videos"
            user_video_folder_fs = os.path.join(current_app.static_folder, user_video_folder_rel)
            os.makedirs(user_video_folder_fs, exist_ok=True)

            for vf in video_files:
                if vf and vf.filename and allowed_file(vf.filename):
                    video_filename = secure_filename(vf.filename)
                    vf.save(os.path.join(user_video_folder_fs, video_filename))
                    video_rel_path = f"{user_video_folder_rel}/{video_filename}"
                    db.session.add(ProductVideo(product_id=new_product.id, video_url=video_rel_path))

        # Package components (same logic)
        if form.is_package.data:
            names = request.form.getlist('component_name[]')
            prices = request.form.getlist('component_price[]')
            comp_files = request.files.getlist('component_image[]')

            for i, cname in enumerate(names):
                cname = cname.strip()
                if not cname:
                    continue
                try:
                    cprice = float(prices[i]) if i < len(prices) and prices[i] else 0.0
                except ValueError:
                    cprice = 0.0

                cimage_file = comp_files[i] if i < len(comp_files) else None
                comp_image_rel = None
                if cimage_file and allowed_file(cimage_file.filename):
                    comp_filename = secure_filename(cimage_file.filename)
                    cimage_file.save(os.path.join(user_folder_fs, comp_filename))
                    comp_image_rel = f"{user_folder_rel}/{comp_filename}"

                db.session.add(ProductComponent(
                    product_id=new_product.id,
                    name=cname,
                    price=cprice,
                    image_url=comp_image_rel
                ))

        db.session.commit()
        flash('Product saved successfully (pending approval).', 'success')
        return redirect(url_for('seller_routes.seller_dashboard'))

    # If we get here, either GET or validation failed.
    if request.method == 'POST' and form.errors:
        # flash form errors so you see what's failing
        for field, errs in form.errors.items():
            flash(f"{field}: {', '.join(errs)}", 'danger')
        # also log for server debugging
        current_app.logger.debug("Form errors: %s", form.errors)
        current_app.logger.debug("request.form (subset): %s", {k: request.form.get(k) for k in ('category','name','price')})

    # prepare category choices for the template UI
    category_choices = form.category.choices
    selected_category_id = form.category.data or ''
    selected_category_label = dict(category_choices).get(selected_category_id, 'Choose category')

    return render_template('seller/add_product.html',
                       form=form,
                       seller_location=seller_location,
                       category_choices=category_choices,
                       selected_category_id=selected_category_id,
                       selected_category_label=selected_category_label)

# Helper function (utils)
def _fs_path_for_rel(rel_path):
    """
    Return absolute filesystem path for a stored relative path like:
    'users/joseph_1/product_images/xxx.jpg' or 'static/users/...'
    """
    if not rel_path:
        return None
    p = rel_path.replace('\\', '/').lstrip('/')
    # if someone accidentally saved 'static/...' in DB, strip it
    if p.startswith('static/'):
        p = p[len('static/'):]
    return os.path.join(current_app.static_folder, p)

def _safe_remove_file(rel_path):
    p = _fs_path_for_rel(rel_path)
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except Exception as e:
        current_app.logger.warning("Failed to remove file %s: %s", p, e)

@seller_bp.route('/product/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    # load and permission check
    product = Product.query.get_or_404(product_id)
    if current_user.role != 'seller' or product.seller_id != current_user.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('routes.marketplace'))

    seller_profile = Seller.query.filter_by(user_id=current_user.id).first()
    seller_location = seller_profile.location if seller_profile else ''

    # instantiate form and set choices early (so WTForms can validate)
    form = ProductForm(obj=product)
    form.category.choices = get_category_choices()

    # custom picker: ensure field populated from hidden input if needed
    if request.method == 'POST':
        current_app.logger.debug("edit_product POST form keys: %s", list(request.form.keys()))
        if not form.category.data:
            cat_raw = request.form.get('category')
            if cat_raw:
                try:
                    form.category.data = int(cat_raw)
                except (ValueError, TypeError):
                    form.category.data = None

    if form.validate_on_submit():
        # server-side category check
        cat_id = form.category.data
        category_obj = Category.query.get(cat_id)
        if not category_obj:
            flash('Invalid category selected.', 'danger')
            return redirect(url_for('seller_routes.edit_product', product_id=product.id))

        # update main fields
        product.name = form.name.data
        product.description = form.description.data
        product.price = float(form.price.data)
        product.category_id = cat_id
        product.location = form.location.data or seller_location
        product.condition = form.condition.data
        product.brand = form.brand.data
        product.gender = form.gender.data
        product.color = form.color.data
        product.size = form.size.data
        product.is_package = bool(form.is_package.data)
        # keep status as-is (or update to 'Pending' if you want re-approval)
        # product.status = 'Pending'

        username_safe = secure_filename(current_user.username)
        # file folders
        user_images_rel = f"users/{username_safe}_{current_user.id}/product_images"
        user_images_fs = os.path.join(current_app.static_folder, user_images_rel)
        os.makedirs(user_images_fs, exist_ok=True)

        user_videos_rel = f"users/{username_safe}_{current_user.id}/product_videos"
        user_videos_fs = os.path.join(current_app.static_folder, user_videos_rel)
        os.makedirs(user_videos_fs, exist_ok=True)

        # ----- HANDLE IMAGE REMOVALS -----
        # Expect 'remove_image' checkboxes containing ProductImage.id
        ids_to_remove = request.form.getlist('remove_image[]')  # form field names in template
        if ids_to_remove:
            for img_id in ids_to_remove:
                try:
                    img = ProductImage.query.filter_by(id=int(img_id), product_id=product.id).first()
                except (ValueError, TypeError):
                    img = None
                if img:
                    _safe_remove_file(img.image_url)
                    db.session.delete(img)

        # ----- HANDLE NEW IMAGE UPLOADS -----
        for f in request.files.getlist('images'):
            if f and f.filename and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                dest = os.path.join(user_images_fs, filename)
                f.save(dest)
                img_rel_path = f"{user_images_rel}/{filename}"
                db.session.add(ProductImage(product_id=product.id, image_url=img_rel_path))

        # ----- HANDLE VIDEO REMOVAL -----
        # If seller checked remove_video (checkbox single), delete all product videos for this product.
        if request.form.get('remove_video') == 'on':
            existing_videos = ProductVideo.query.filter_by(product_id=product.id).all()
            for ev in existing_videos:
                _safe_remove_file(ev.video_url)
                db.session.delete(ev)

        # ----- HANDLE VIDEO REMOVAL (multiple checkboxes) -----
        video_ids_to_remove = request.form.getlist('remove_video[]')
        if video_ids_to_remove:
            for vid_id in video_ids_to_remove:
                try:
                    pv = ProductVideo.query.filter_by(id=int(vid_id), product_id=product.id).first()
                except (ValueError, TypeError):
                    pv = None
                if pv:
                    _safe_remove_file(pv.video_url)   # your helper to remove files from FS
                    db.session.delete(pv)

        # ----- HANDLE NEW VIDEO UPLOADS (multiple) -----
        new_video_files = request.files.getlist('videos')
        if new_video_files:
            os.makedirs(user_videos_fs, exist_ok=True)
            for vf in new_video_files:
                if vf and vf.filename and allowed_file(vf.filename):
                    vfilename = secure_filename(vf.filename)
                    vf.save(os.path.join(user_videos_fs, vfilename))
                    v_rel = f"{user_videos_rel}/{vfilename}"
                    db.session.add(ProductVideo(product_id=product.id, video_url=v_rel))

        # ----- PACKAGE COMPONENTS -----
        # Strategy: delete old components and recreate from form submission (keeps logic simple)
        if product.is_package:
            # Remove existing components for product
            old_components = ProductComponent.query.filter_by(product_id=product.id).all()
            for oc in old_components:
                # remove file for component image if exists
                if getattr(oc, 'image_url', None):
                    _safe_remove_file(oc.image_url)
                db.session.delete(oc)

            # Create components from submitted arrays (same names as add_product)
            names = request.form.getlist('component_name[]')
            prices = request.form.getlist('component_price[]')
            comp_files = request.files.getlist('component_image[]')

            for i, cname in enumerate(names):
                cname = (cname or '').strip()
                if not cname:
                    continue
                try:
                    cprice = float(prices[i]) if i < len(prices) and prices[i] else 0.0
                except (ValueError, TypeError):
                    cprice = 0.0

                cimage_file = comp_files[i] if i < len(comp_files) else None
                comp_image_rel = None
                if cimage_file and cimage_file.filename and allowed_file(cimage_file.filename):
                    comp_filename = secure_filename(cimage_file.filename)
                    cimage_file.save(os.path.join(user_images_fs, comp_filename))
                    comp_image_rel = f"{user_images_rel}/{comp_filename}"

                db.session.add(ProductComponent(product_id=product.id, name=cname, price=cprice, image_url=comp_image_rel))

        else:
            # if seller turned off 'is_package' remove any old components
            old_components = ProductComponent.query.filter_by(product_id=product.id).all()
            for oc in old_components:
                if getattr(oc, 'image_url', None):
                    _safe_remove_file(oc.image_url)
                db.session.delete(oc)

        db.session.commit()
        flash('Product updated successfully.', 'success')
        # Redirect to seller dashboard or product details (adjust endpoint name to your registers)
        return redirect(url_for('seller_routes.seller_dashboard'))

    # prepare values for the template when rendering GET or validation failed
    category_choices = form.category.choices
    selected_category_id = form.category.data or (product.category_id if product.category_id else '')
    selected_category_label = dict(category_choices).get(selected_category_id, 'Choose category')

    # gather existing images and video(s) to show
    existing_images = ProductImage.query.filter_by(product_id=product.id).all()
    existing_videos = ProductVideo.query.filter_by(product_id=product.id).all()
    existing_components = ProductComponent.query.filter_by(product_id=product.id).all()

    return render_template('seller/edit_product.html',
                           form=form,
                           product=product,
                           seller_location=seller_location,
                           category_choices=category_choices,
                           selected_category_id=selected_category_id,
                           selected_category_label=selected_category_label,
                           existing_images=existing_images,
                           existing_videos=existing_videos,
                           existing_components=existing_components)

@seller_bp.route('/product_details/<int:product_id>', methods=['GET', 'POST'])
@login_required
def product_details(product_id):
    # Load product and eager-load images and seller
    product = Product.query.options(
        joinedload(Product.images),
        joinedload(Product.seller),
        joinedload(Product.category)
    ).get_or_404(product_id)

    # increment view count
    try:
        product.increment_view_count()
    except Exception:
        # avoid breaking page if commit fails
        current_app.logger.debug("Failed to increment view_count for product %s", product_id, exc_info=True)

    # choose base template depending on role
    role = getattr(current_user, 'role', None)
    if role == 'seller':
        base_template = 'seller/base_seller.html'
    elif role == 'affiliate':
        base_template = 'affiliate/base_affiliate.html'
    elif role == 'buyer':
        base_template = 'buyer/base_buyer.html'
    elif role == 'admin':
        base_template = 'admin/base_admin.html'
    else:
        base_template = 'base.html'

    # forms & components (keep your existing logic)
    form = AddToCartForm()
    components = []
    if product.is_package:
        components = ProductComponent.query.filter_by(product_id=product_id).all()
        try:
            form.components.choices = [(str(c.id), c.name) for c in components]
        except Exception:
            pass
        try:
            while len(form.quantities) < len(components):
                form.quantities.append_entry(1)
        except Exception:
            pass

    if form.validate_on_submit():
        # your add-to-cart handling (unchanged)
        pass

    # Helper: robust product image builder
    def build_product_image_url(p):
        try:
            if getattr(p, 'images', None):
                first = p.images[0]
                img_url = getattr(first, 'url', None) or getattr(first, 'image_url', None)
                if img_url:
                    path = img_url.replace('\\', '/').lstrip('/')
                    if path.startswith('static/'):
                        path = path[len('static/'):]
                    return url_for('static', filename=path)
        except Exception:
            current_app.logger.debug("Error building product image url for product %s", getattr(p, 'id', None), exc_info=True)
        return url_for('static', filename='default_image.jpg')

    # Build image_urls for the main product images
    image_urls = []
    if product.images:
        for img in product.images:
            img_url = getattr(img, 'url', None) or getattr(img, 'image_url', None)
            if img_url:
                path = img_url.replace('\\', '/').lstrip('/')
                if path.startswith('static/'):
                    path = path[len('static/'):]
                image_urls.append(url_for('static', filename=path))
    if not image_urls:
        image_urls = [url_for('static', filename='default_image.jpg')]

    # QR codes (unchanged)
    qr_urls = []
    qr_dir = os.path.join(current_app.static_folder, 'qr_codes')
    if os.path.isdir(qr_dir):
        for fname in sorted(os.listdir(qr_dir)):
            if not fname.lower().endswith(('.png', '.jpg', '.jpeg', '.svg', '.webp')):
                continue
            qr_urls.append(url_for('static', filename=f'qr_codes/{fname}'))

    product_qr_url = None
    if product.qr_code:
        fname = os.path.basename(product.qr_code).replace('\\', '/')
        potential = os.path.join(qr_dir, fname)
        if os.path.isfile(potential):
            product_qr_url = url_for('static', filename=f'qr_codes/{fname}')

    # Role-specific affiliate / seller data (kept as you had it)
    affiliate_referral_link = None
    existing_referral_id = None
    affiliates_stats = []
    # ... existing affiliate/seller code unchanged (omitted here for brevity) ...

    # --------------------------
    # Similar products logic (by seller)
    # --------------------------
    similar_products = []

    # Accept either 'active' or 'available' as active statuses - adjust to your DB values
    ACTIVE_STATUSES = ['active', 'available']

    # 1) products from same seller (exclude current)
    same_seller_q = Product.query.options(joinedload(Product.images), joinedload(Product.seller)).filter(
        Product.id != product.id,
        Product.seller_id == product.seller_id,
        Product.status.in_(ACTIVE_STATUSES)
    ).limit(8).all()

    # 2) same brand (if seller has less than 8 products)
    same_brand_q = []
    if product.brand:
        same_brand_q = Product.query.options(joinedload(Product.images), joinedload(Product.seller)).filter(
            Product.id != product.id,
            Product.brand == product.brand,
            Product.status.in_(ACTIVE_STATUSES)
        ).limit(8).all()

    # 3) price proximity (±30%)
    price_q = []
    try:
        if product.price and product.price > 0:
            lo = product.price * 0.7
            hi = product.price * 1.3
            price_q = Product.query.options(joinedload(Product.images), joinedload(Product.seller)).filter(
                Product.id != product.id,
                Product.price.between(lo, hi),
                Product.status.in_(ACTIVE_STATUSES)
            ).limit(8).all()
    except Exception:
        current_app.logger.debug("Price proximity query failed for product %s", product.id, exc_info=True)

    # Merge unique lists in desired priority: same seller → brand → price
    seen = set()
    def push_list(lst):
        for p in lst:
            if not p or getattr(p, 'id', None) in seen or p.id == product.id:
                continue
            seen.add(p.id)
            similar_products.append(p)
            if len(similar_products) >= 8:
                break

    push_list(same_seller_q)
    if len(similar_products) < 8:
        push_list(same_brand_q)
    if len(similar_products) < 8:
        push_list(price_q)

    # Serialize for template
    sp_serialized = []
    for sp in similar_products:
        sp_serialized.append({
            "id": sp.id,
            "name": sp.name,
            "price": float(sp.price or 0.0),
            "image_url": build_product_image_url(sp),
            "seller_name": getattr(sp.seller, 'username', 'Seller')
        })
        
    return render_template(
        'product_details.html',
        base_template=base_template,
        product=product,
        form=form,
        image_urls=image_urls,
        qr_urls=qr_urls,
        product_qr_url=product_qr_url,
        components=components,
        affiliate_referral_link=affiliate_referral_link,
        existing_referral_id=existing_referral_id,
        affiliates_stats=affiliates_stats,
        similar_products=sp_serialized
    )

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
        return redirect(url_for('seller_routes.dashboard'))    
    # If GET request (for confirmation page or modal)
    form = FlaskForm()  # Create an empty form for CSRF token
    return render_template('delete_product.html', product=product, form=form)

#----------------AFFILIATE MARKETING--------------------
@seller_bp.route('/affiliate_marketing')
@login_required
def affiliate_marketing():
    # only sellers allowed
    if not getattr(current_user, 'is_seller', False):
        abort(403)

    seller = Seller.query.filter_by(user_id=current_user.id).first_or_404()

    # seller products (optional use in UI)
    products = Product.query.filter_by(seller_id=seller.id).all()

    # aggregate affiliates who have referrals for this seller's products
    rows = (
        db.session.query(
            Affiliate,
            func.count(Referral.id).label('ref_count'),
            func.coalesce(func.sum(Referral.commission), 0).label('total_commission'),
            func.max(Referral.timestamp).label('last_referred')
        )
        .join(Referral, Affiliate.id == Referral.affiliate_id)
        .join(Product, Product.id == Referral.product_id)
        .filter(Product.seller_id == seller.id)
        .group_by(Affiliate.id)
        .order_by(desc('total_commission'))
        .all()
    )

    affiliates = []
    for aff, ref_count, total_commission, last_referred in rows:
        affiliates.append({
            'affiliate': aff,
            'ref_count': int(ref_count),
            'total_commission': float(total_commission or 0.0),
            'last_referred': last_referred
        })

    return render_template(
        'seller/affiliate_marketing.html',
        seller=seller,
        products=products,
        affiliates=affiliates
    )

@seller_bp.route('/affiliate/<int:affiliate_id>')
@login_required
def affiliate_detail(affiliate_id):
    if not getattr(current_user, 'is_seller', False):
        abort(403)
    seller = Seller.query.filter_by(user_id=current_user.id).first_or_404()
    affiliate = Affiliate.query.get_or_404(affiliate_id)

    # fetch referrals made by this affiliate for this seller's products
    referrals = (
        Referral.query
        .join(Product, Product.id == Referral.product_id)
        .filter(Referral.affiliate_id == affiliate.id, Product.seller_id == seller.id)
        .order_by(Referral.timestamp.desc())
        .all()
    )

    return render_template('seller/affiliate_detail.html',
                           seller=seller,
                           affiliate=affiliate,
                           referrals=referrals)

#-----------------PAYMENTS--------------------  
@seller_bp.route('/payments', methods=['GET'])
@login_required
def payments():
    if current_user.role != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('routes.index'))

    # For now just render an empty template (create payments.html in templates/seller/)
    return render_template('seller/payments.html', active='payments')

####################
@seller_bp.route('/available_subscriptions', methods=['GET'])
@login_required
def available_seller_subscriptions():
    if not current_user.is_seller:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    # Fetch active subscriptions
    active_subscriptions = Subscription.query.filter_by(status='active').all()

    # Ensure seller profile exists
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        user = User.query.get(current_user.id)
        if not user:
            flash("User profile not found. Please contact support.", "danger")
            return redirect(url_for('index'))
        seller = Seller(user_id=user.id, username=user.username or f"seller_{user.id}", email=user.email or user.email)
        db.session.add(seller)
        db.session.commit()
        flash("Seller profile was missing and has been created.", "info")

    # Get all subscription ids seller already has (via association table)
    subscribed_rows = SellerSubscription.query.filter_by(seller_id=seller.id).all()
    subscribed_ids = {row.subscription_id for row in subscribed_rows}

    # Only show active plans the seller hasn't subscribed to
    unsubscribed_subscriptions = [s for s in active_subscriptions if s.id not in subscribed_ids]

    if not unsubscribed_subscriptions:
        flash("You have already subscribed to all available plans.", "info")

    # Also pass current subscriptions for UI if needed
    subscribed_subscriptions = [row.subscription for row in subscribed_rows]

    return render_template(
        'seller/available_seller_subscriptions.html',
        unsubscribed_subscriptions=unsubscribed_subscriptions,
        subscribed_subscriptions=subscribed_subscriptions
    )


@seller_bp.route('/subscriptions/<int:subscription_id>/toggle', methods=['POST'])
@login_required
def toggle_seller_subscription(subscription_id):
    """Subscribe, unsubscribe, or switch subscription."""
    if not current_user.is_seller:
        flash("Access denied.", "danger")
        return redirect(url_for('routes.index'))

    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        flash("Seller profile missing.", "danger")
        return redirect(url_for('seller_routes.available_seller_subscriptions'))

    subscription = Subscription.query.get_or_404(subscription_id)

    # Check existing active subscription
    active_sub = SellerSubscription.query.filter_by(
        seller_id=seller.id
    ).filter(SellerSubscription.valid_until >= datetime.utcnow()).first()

    if active_sub:
        # If user clicks the same plan -> unsubscribe
        if active_sub.subscription_id == subscription.id:
            db.session.delete(active_sub)
            db.session.commit()
            flash(f"Unsubscribed from {subscription.name}.", "success")
            return redirect(url_for('seller_routes.my_subscriptions'))
        else:
            # Switching plans (upgrade/downgrade)
            flash(f"You have an active subscription. You must switch from {active_sub.subscription.name} to {subscription.name}.", "info")
            return redirect(url_for('seller_routes.confirm_subscription_payment', subscription_id=subscription.id))

    # No active subscription, proceed to payment
    return redirect(url_for('seller_routes.confirm_subscription_payment', subscription_id=subscription.id))


@seller_bp.route('/subscriptions/<int:subscription_id>/confirm_payment')
@login_required
def confirm_subscription_payment(subscription_id):
    """Show payment confirmation page before charging wallet."""
    subscription = Subscription.query.get_or_404(subscription_id)
    wallet = get_or_create_wallet(current_user.id)
    
    # Get active subscription
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    active_sub = SellerSubscription.query.filter_by(seller_id=seller.id).filter(SellerSubscription.valid_until >= datetime.utcnow()).first()

    return render_template(
        'seller/confirm_subscription_payment.html',
        subscription=subscription,
        wallet=wallet,
        active_sub=active_sub
    )


@seller_bp.route('/subscriptions/<int:subscription_id>/process_payment', methods=['POST'])
@login_required
def process_subscription_payment(subscription_id):
    """Process wallet payment and activate subscription."""
    subscription = Subscription.query.get_or_404(subscription_id)
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    wallet = get_or_create_wallet(current_user.id)
    amount = Decimal(subscription.price or 0)

    if Decimal(wallet.balance) < amount:
        flash("Insufficient wallet balance. Please top up and try again.", "danger")
        return redirect(url_for('user_routes.wallet_page'))

    try:
        # Deduct from wallet
        debit_wallet(
            wallet=wallet,
            amount=amount,
            source="subscription",
            reference_type="subscription",
            reference_id=subscription.id,
            description=f"Payment for {subscription.name}",
            status='completed'
        )
        db.session.commit()

        # Cancel previous active subscription if exists
        active_sub = SellerSubscription.query.filter_by(seller_id=seller.id)\
            .filter(SellerSubscription.valid_until >= datetime.utcnow()).first()
        if active_sub:
            db.session.delete(active_sub)
            db.session.commit()
        
        # Activate new subscription
        valid_until = datetime.utcnow() + timedelta(days=subscription.validity_period)
        new_subscription = SellerSubscription(
            seller_id=seller.id,
            subscription_id=subscription.id,
            subscribed_on=datetime.utcnow(),
            valid_until=valid_until
        )
        db.session.add(new_subscription)
        db.session.commit()

        flash(f"Subscription successful! {subscription.name} is active until {valid_until.date()}.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Payment failed: {str(e)}", "danger")

    # Query the seller's current subscriptions to pass to template
    subscriptions = Subscription.query.join(SellerSubscription)\
        .filter(SellerSubscription.seller_id == seller.id).all()

    return render_template(
        'seller/my_subscriptions.html',
        subscriptions=subscriptions,
        current_date=datetime.utcnow()
    )

@seller_bp.route('/my_subscriptions', methods=['GET'])
@login_required
def my_subscriptions():
    if not current_user.is_seller:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        flash("Seller profile missing.", "danger")
        return redirect(url_for('routes.index'))

    current_date = datetime.utcnow()

    # Get seller's active subscription
    active_sub = SellerSubscription.query.filter_by(seller_id=seller.id)\
        .filter(SellerSubscription.valid_until >= current_date).first()

    if not active_sub:
        flash("You currently have no active subscription.", "info")
        return render_template('seller/my_subscriptions.html', active_sub=None)

    # Fetch all active plans excluding current one
    all_active_plans = Subscription.query.filter(Subscription.status == 'active')\
        .filter(Subscription.id != active_sub.subscription_id).all()

    # Determine if upgrade/downgrade exists
    upgrade_plan = None
    downgrade_plan = None
    for plan in all_active_plans:
        if plan.price > active_sub.subscription.price:
            if not upgrade_plan or plan.price < upgrade_plan.price:
                upgrade_plan = plan
        elif plan.price < active_sub.subscription.price:
            if not downgrade_plan or plan.price > downgrade_plan.price:
                downgrade_plan = plan

    return render_template(
        'seller/my_subscriptions.html',
        active_sub=active_sub,
        upgrade_plan=upgrade_plan,
        downgrade_plan=downgrade_plan,
        current_date=current_date
    )


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

#-----------------CHAT FUNCTIONALITY--------------------
@seller_bp.route('/chat')
@login_required
def seller_chat():
    return render_template('chat_page.html', user=current_user)
