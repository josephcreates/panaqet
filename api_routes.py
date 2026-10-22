import os
import uuid
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, session, current_app, send_from_directory, url_for
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename

from database import db
from models import (
    User, Admin, Buyer, Seller, Product, ProductImage, ProductComponent,
    Cart, SavedProduct, Order, OrderItem, Subscription, SellerSubscription,
    CommissionPlan, Affiliate, Referral, AffiliateSignup, Conversation, Message
)

api_bp = Blueprint('api_routes', __name__, url_prefix='/api')

def api_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Unauthorized. Please log in.'}), 401
        return f(*args, **kwargs)
    return decorated_function

def api_role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'error': 'Unauthorized. Please log in.'}), 401
            user_role = getattr(current_user, 'role', None)
            if user_role not in roles:
                return jsonify({'error': f'Forbidden. Required role: {", ".join(roles)}'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi', 'mov'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def serialize_user(user):
    if not user:
        return None
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': getattr(user, 'role', 'admin'),
        'country_code': getattr(user, 'country_code', ''),
        'phone_number': getattr(user, 'phone_number', ''),
        'country': getattr(user, 'country', ''),
        'profile_image': user.profile_image if getattr(user, 'profile_image', None) else 'default_profile.png',
        'signup_complete': getattr(user, 'signup_complete', True),
        'store_name': getattr(user, 'store_name', None),
        'store_description': getattr(user, 'store_description', None),
        'store_logo': getattr(user, 'store_logo', None),
        'user_theme': getattr(user, 'user_theme', 'light'),
        'preferred_language': getattr(user, 'preferred_language', 'en')
    }

def serialize_product(product):
    if not product:
        return None
    images = [img.image_url.replace('\\', '/') for img in product.images] if product.images else []
    components = [{'id': c.id, 'name': c.name, 'price': c.price} for c in product.components] if product.components else []
    seller_name = product.seller.username if product.seller else ''
    store_name = product.seller.user.store_name if (product.seller and product.seller.user and product.seller.user.store_name) else seller_name

    return {
        'id': product.id,
        'name': product.name,
        'description': product.description,
        'price': product.price,
        'seller_id': product.seller_id,
        'seller_name': seller_name,
        'store_name': store_name,
        'date_added': product.date_added.strftime('%Y-%m-%d %H:%M:%S') if product.date_added else None,
        'status': product.status,
        'category': product.category,
        'location': product.location,
        'condition': product.condition,
        'brand': product.brand,
        'gender': product.gender,
        'color': product.color,
        'size': product.size,
        'is_package': product.is_package,
        'qr_code': product.qr_code,
        'view_count': product.view_count,
        'images': images,
        'components': components
    }

# ================= AUTHENTICATION ENDPOINTS =================

@api_bp.route('/auth/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or request.form
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    admin = Admin.query.filter_by(email=email).first()
    if admin and admin.check_password(password):
        login_user(admin)
        session['user_type'] = 'admin'
        return jsonify({
            'message': 'Admin login successful',
            'user': {
                'id': admin.id,
                'username': admin.username,
                'email': admin.email,
                'role': 'admin',
                'user_theme': 'light',
                'preferred_language': 'en'
            }
        }), 200

    user = User.query.filter_by(email=email).first()
    if user and user.check_password(password):
        login_user(user)
        session['user_type'] = 'user'
        return jsonify({
            'message': 'Login successful',
            'user': serialize_user(user)
        }), 200

    return jsonify({'error': 'Invalid email or password'}), 401

@api_bp.route('/auth/signup', methods=['POST'])
def api_signup():
    data = request.get_json(silent=True) or request.form
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'buyer').strip().lower()
    country = data.get('country', '').strip()
    country_code = data.get('country_code', '').strip()
    phone_number = data.get('phone_number', '').strip()

    if not username or not email or not password or not country:
        return jsonify({'error': 'All required fields (username, email, password, country) must be provided'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 400

    try:
        new_user = User(
            username=username,
            email=email,
            country_code=country_code,
            phone_number=phone_number,
            country=country,
            role=role,
            signup_complete=(role == 'buyer')
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        if role == 'buyer':
            new_buyer = Buyer(user_id=new_user.id, username=username, email=email)
            db.session.add(new_buyer)
        elif role == 'seller':
            new_seller = Seller(user_id=new_user.id, username=username, email=email, location=country)
            db.session.add(new_seller)
        elif role == 'affiliate':
            ref_code = str(uuid.uuid4().hex[:8]).upper()
            new_affiliate = Affiliate(user_id=new_user.id, username=username, email=email, referral_code=ref_code)
            db.session.add(new_affiliate)

        db.session.commit()
        login_user(new_user)
        session['user_type'] = 'user'

        return jsonify({
            'message': 'Signup successful',
            'user': serialize_user(new_user)
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create user: {str(e)}'}), 500

@api_bp.route('/auth/signup-complete', methods=['POST'])
@api_login_required
def api_signup_complete():
    id_type = request.form.get('id_type', '').strip()
    id_front = request.files.get('id_front')
    id_back = request.files.get('id_back')

    if not id_type:
        return jsonify({'error': 'ID type is required'}), 400

    if current_user.role in ['seller', 'affiliate'] and (not id_front or not id_back):
        return jsonify({'error': 'Front and back ID images are required for sellers and affiliates'}), 400

    try:
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

        return jsonify({
            'message': 'Signup completed successfully',
            'user': serialize_user(current_user)
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error completing profile: {str(e)}'}), 500

@api_bp.route('/auth/me', methods=['GET'])
@api_login_required
def api_me():
    return jsonify({'user': serialize_user(current_user)}), 200

@api_bp.route('/auth/logout', methods=['POST'])
@api_login_required
def api_logout():
    logout_user()
    session.clear()
    return jsonify({'message': 'Logged out successfully'}), 200

@api_bp.route('/auth/profile', methods=['PUT'])
@api_login_required
def api_update_profile():
    data = request.form if request.files else (request.get_json(silent=True) or {})
    username = data.get('username', current_user.username).strip()
    email = data.get('email', current_user.email).strip()
    country = data.get('country', current_user.country).strip()
    phone_number = data.get('phone_number', current_user.phone_number).strip()
    country_code = data.get('country_code', current_user.country_code).strip()

    current_user.username = username
    current_user.email = email
    current_user.country = country
    current_user.phone_number = phone_number
    current_user.country_code = country_code

    if 'profile_image' in request.files:
        file = request.files['profile_image']
        if file and allowed_file(file.filename):
            user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'profile_images')
            os.makedirs(user_folder, exist_ok=True)
            filename = secure_filename(file.filename)
            file_path = os.path.join(user_folder, filename)
            file.save(file_path)
            current_user.profile_image = f"users/{current_user.username}_{current_user.id}/profile_images/{filename}"

    db.session.commit()
    return jsonify({'message': 'Profile updated successfully', 'user': serialize_user(current_user)}), 200

@api_bp.route('/auth/settings', methods=['PUT'])
@api_login_required
def api_update_settings():
    data = request.get_json(silent=True) or request.form
    theme = data.get('theme')
    language = data.get('language')

    if theme and theme in ['default', 'light', 'dark', 'blue', 'green', 'red']:
        current_user.user_theme = theme
    if language and language in ['en', 'fr', 'pt', 'it', 'es']:
        current_user.preferred_language = language

    db.session.commit()
    return jsonify({'message': 'Settings updated', 'user': serialize_user(current_user)}), 200

# ================= MARKETPLACE & BUYER ENDPOINTS =================

@api_bp.route('/marketplace/products', methods=['GET'])
def api_get_products():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 12, type=int)
    search_query = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    location = request.args.get('location', '').strip()
    sort = request.args.get('sort', 'newest').strip()

    query = Product.query.filter_by(status='Approved')

    if search_query:
        pattern = f'%{search_query}%'
        query = query.filter((Product.name.ilike(pattern)) | (Product.description.ilike(pattern)))
    if category:
        query = query.filter(Product.category == category)
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)
    if location:
        query = query.filter(Product.location.ilike(f'%{location}%'))

    if sort == 'price_asc':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Product.price.desc())
    elif sort == 'popular':
        query = query.order_by(Product.view_count.desc())
    else:
        query = query.order_by(Product.date_added.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'products': [serialize_product(p) for p in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev
    }), 200

@api_bp.route('/marketplace/products/<int:product_id>', methods=['GET'])
def api_get_product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    product.increment_view_count()
    return jsonify({'product': serialize_product(product)}), 200

@api_bp.route('/marketplace/categories', methods=['GET'])
def api_get_categories():
    categories = [c[0] for c in db.session.query(Product.category).distinct().all() if c[0]]
    return jsonify({'categories': categories}), 200

@api_bp.route('/buyer/cart', methods=['GET'])
@api_login_required
def api_get_cart():
    cart_items = Cart.query.filter_by(user_id=current_user.id).all()
    items_data = []
    total_amount = 0.0

    for item in cart_items:
        if not item.product:
            continue
        p_price = item.product.price
        if item.component:
            p_price += item.component.price
        item_total = p_price * item.quantity
        total_amount += item_total

        items_data.append({
            'cart_id': item.id,
            'product_id': item.product_id,
            'product_name': item.product.name,
            'price': item.product.price,
            'quantity': item.quantity,
            'component_id': item.component_id,
            'component_name': item.component.name if item.component else None,
            'component_price': item.component.price if item.component else 0.0,
            'item_total': item_total,
            'image_url': item.product.images[0].image_url.replace('\\', '/') if item.product.images else None
        })

    return jsonify({
        'cart_items': items_data,
        'total_amount': total_amount
    }), 200

@api_bp.route('/buyer/cart/add', methods=['POST'])
@api_login_required
def api_add_to_cart():
    data = request.get_json(silent=True) or request.form
    product_id = data.get('product_id', type=int)
    quantity = data.get('quantity', 1, type=int)
    component_id = data.get('component_id', type=int)

    if not product_id:
        return jsonify({'error': 'Product ID is required'}), 400

    product = Product.query.get(product_id)
    if not product:
        return jsonify({'error': 'Product not found'}), 404

    cart_item = Cart.query.filter_by(user_id=current_user.id, product_id=product_id, component_id=component_id).first()
    if cart_item:
        cart_item.quantity += quantity
    else:
        cart_item = Cart(user_id=current_user.id, product_id=product_id, component_id=component_id, quantity=quantity)
        db.session.add(cart_item)

    db.session.commit()
    return jsonify({'message': 'Added to cart successfully'}), 200

@api_bp.route('/buyer/cart/remove/<int:cart_id>', methods=['DELETE', 'POST'])
@api_login_required
def api_remove_from_cart(cart_id):
    cart_item = Cart.query.get(cart_id)
    if cart_item and cart_item.user_id == current_user.id:
        db.session.delete(cart_item)
        db.session.commit()
        return jsonify({'message': 'Item removed from cart'}), 200
    return jsonify({'error': 'Cart item not found or unauthorized'}), 404

@api_bp.route('/buyer/cart/clear', methods=['POST'])
@api_login_required
def api_clear_cart():
    Cart.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({'message': 'Cart cleared'}), 200

@api_bp.route('/buyer/wishlist', methods=['GET'])
@api_login_required
def api_get_wishlist():
    saved_items = SavedProduct.query.filter_by(user_id=current_user.id).all()
    wishlist = [serialize_product(item.product) for item in saved_items if item.product]
    return jsonify({'wishlist': wishlist}), 200

@api_bp.route('/buyer/wishlist/save/<int:product_id>', methods=['POST'])
@api_login_required
def api_save_to_wishlist(product_id):
    existing = SavedProduct.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if existing:
        return jsonify({'message': 'Already in wishlist'}), 200

    saved = SavedProduct(user_id=current_user.id, product_id=product_id)
    db.session.add(saved)
    db.session.commit()
    return jsonify({'message': 'Product saved to wishlist'}), 201

@api_bp.route('/buyer/wishlist/remove/<int:product_id>', methods=['DELETE', 'POST'])
@api_login_required
def api_remove_from_wishlist(product_id):
    saved = SavedProduct.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if saved:
        db.session.delete(saved)
        db.session.commit()
        return jsonify({'message': 'Product removed from wishlist'}), 200
    return jsonify({'error': 'Product not found in wishlist'}), 404

@api_bp.route('/buyer/checkout', methods=['POST'])
@api_login_required
def api_checkout():
    data = request.get_json(silent=True) or request.form
    name = data.get('name', '').strip()
    address = data.get('address', '').strip()
    payment_method = data.get('payment_method', 'Cash').strip()

    cart_items = Cart.query.filter_by(user_id=current_user.id).all()
    if not cart_items:
        return jsonify({'error': 'Cart is empty'}), 400

    buyer = Buyer.query.filter_by(user_id=current_user.id).first()
    if not buyer:
        buyer = Buyer(user_id=current_user.id, username=current_user.username, email=current_user.email)
        db.session.add(buyer)
        db.session.commit()

    total_amount = 0.0
    order_items = []

    for item in cart_items:
        if not item.product:
            continue
        unit_price = item.product.price + (item.component.price if item.component else 0.0)
        item_total = unit_price * item.quantity
        total_amount += item_total

        order_item = OrderItem(
            product_id=item.product_id,
            quantity=item.quantity,
            total_price=item_total
        )
        order_items.append(order_item)

    new_order = Order(
        buyer_id=buyer.id,
        total_amount=total_amount
    )
    db.session.add(new_order)
    db.session.commit()

    for item in order_items:
        item.order_id = new_order.id
        db.session.add(item)

    Cart.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()

    return jsonify({
        'message': 'Order placed successfully',
        'order_id': new_order.id,
        'total_amount': total_amount,
        'order_date': new_order.order_date.strftime('%Y-%m-%d %H:%M:%S')
    }), 201

@api_bp.route('/buyer/orders', methods=['GET'])
@api_login_required
def api_get_buyer_orders():
    buyer = Buyer.query.filter_by(user_id=current_user.id).first()
    if not buyer:
        return jsonify({'orders': []}), 200

    orders = Order.query.filter_by(buyer_id=buyer.id).order_by(Order.order_date.desc()).all()
    orders_data = []

    for order in orders:
        items = [{
            'id': item.id,
            'product_id': item.product_id,
            'product_name': item.product.name if item.product else 'Unknown',
            'quantity': item.quantity,
            'total_price': item.total_price,
            'image_url': item.product.images[0].image_url.replace('\\', '/') if (item.product and item.product.images) else None
        } for item in order.items]

        orders_data.append({
            'id': order.id,
            'total_amount': order.total_amount,
            'order_date': order.order_date.strftime('%Y-%m-%d %H:%M:%S'),
            'items': items
        })

    return jsonify({'orders': orders_data}), 200

# ================= SELLER ENDPOINTS =================

@api_bp.route('/seller/dashboard', methods=['GET'])
@api_login_required
@api_role_required('seller')
def api_seller_dashboard():
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        return jsonify({'error': 'Seller profile not found'}), 404

    products = Product.query.filter_by(seller_id=seller.id).all()
    total_products = len(products)
    approved_products = [serialize_product(p) for p in products if p.status == 'Approved']
    pending_products = [serialize_product(p) for p in products if p.status == 'Pending']

    total_sales = db.session.query(db.func.sum(Product.price)).filter(Product.seller_id == seller.id).scalar() or 0.0

    seller_orders_query = db.session.query(Order, OrderItem, Product)\
        .join(OrderItem, OrderItem.order_id == Order.id)\
        .join(Product, Product.id == OrderItem.product_id)\
        .filter(Product.seller_id == seller.id).all()

    orders_list = []
    for order, item, product in seller_orders_query:
        orders_list.append({
            'order_id': order.id,
            'buyer_id': order.buyer_id,
            'buyer_name': order.buyer.username if order.buyer else 'Buyer',
            'product_id': product.id,
            'product_name': product.name,
            'quantity': item.quantity,
            'total_price': item.total_price,
            'order_date': order.order_date.strftime('%Y-%m-%d %H:%M:%S')
        })

    return jsonify({
        'total_products': total_products,
        'total_sales': total_sales,
        'approved_products': approved_products,
        'pending_products': pending_products,
        'orders': orders_list,
        'store_name': current_user.store_name,
        'store_description': current_user.store_description,
        'store_logo': current_user.store_logo
    }), 200

@api_bp.route('/seller/products', methods=['POST'])
@api_login_required
@api_role_required('seller')
def api_add_product():
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        return jsonify({'error': 'Seller profile not found'}), 404

    data = request.form
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    price = data.get('price', 0.0, type=float)
    category = data.get('category', '').strip()
    location = data.get('location', '').strip()
    condition = data.get('condition', '').strip()
    brand = data.get('brand', '').strip()
    gender = data.get('gender', '').strip()
    color = data.get('color', '').strip()
    size = data.get('size', '').strip()
    is_package = data.get('is_package', 'false').lower() == 'true'

    if not name or price <= 0:
        return jsonify({'error': 'Product name and a valid price are required'}), 400

    new_product = Product(
        name=name,
        description=description,
        price=price,
        category=category,
        seller_id=seller.id,
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
    db.session.commit()

    if 'images' in request.files:
        files = request.files.getlist('images')
        user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'product_images')
        os.makedirs(user_folder, exist_ok=True)

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(user_folder, filename)
                file.save(file_path)

                image_url = f"users/{current_user.username}_{current_user.id}/product_images/{filename}"
                product_image = ProductImage(product_id=new_product.id, image_url=image_url)
                db.session.add(product_image)

        db.session.commit()

    return jsonify({'message': 'Product added and is pending admin approval', 'product': serialize_product(new_product)}), 201

@api_bp.route('/seller/products/<int:product_id>', methods=['PUT', 'DELETE'])
@api_login_required
@api_role_required('seller', 'admin')
def api_manage_seller_product(product_id):
    product = Product.query.get_or_404(product_id)
    seller = Seller.query.filter_by(user_id=current_user.id).first()

    if current_user.role != 'admin' and (not seller or product.seller_id != seller.id):
        return jsonify({'error': 'Forbidden'}), 403

    if request.method == 'DELETE':
        db.session.delete(product)
        db.session.commit()
        return jsonify({'message': 'Product deleted successfully'}), 200

    data = request.get_json(silent=True) or request.form
    product.name = data.get('name', product.name).strip()
    product.description = data.get('description', product.description).strip()
    product.price = data.get('price', product.price, type=float)
    product.category = data.get('category', product.category).strip()
    product.location = data.get('location', product.location).strip()
    product.condition = data.get('condition', product.condition).strip()
    product.brand = data.get('brand', product.brand).strip()

    db.session.commit()
    return jsonify({'message': 'Product updated', 'product': serialize_product(product)}), 200

@api_bp.route('/seller/store-setup', methods=['POST'])
@api_login_required
@api_role_required('seller')
def api_store_setup():
    store_name = request.form.get('store_name', '').strip()
    store_description = request.form.get('store_description', '').strip()
    store_logo = request.files.get('store_logo')

    if store_name:
        current_user.store_name = store_name
    if store_description:
        current_user.store_description = store_description

    if store_logo and allowed_file(store_logo.filename):
        logo_filename = f"{current_user.username}_store_logo.png"
        logo_path = os.path.join(current_app.config['STORE_LOGOS_FOLDER'], logo_filename)
        os.makedirs(current_app.config['STORE_LOGOS_FOLDER'], exist_ok=True)
        store_logo.save(logo_path)
        current_user.store_logo = logo_filename

    db.session.commit()
    return jsonify({
        'message': 'Store setup saved successfully',
        'user': serialize_user(current_user)
    }), 200

@api_bp.route('/seller/store/<int:seller_id>', methods=['GET'])
def api_get_seller_store(seller_id):
    seller_user = User.query.filter_by(id=seller_id, role='seller').first()
    if not seller_user:
        return jsonify({'error': 'Seller not found'}), 404

    seller = Seller.query.filter_by(user_id=seller_user.id).first()
    s_id = seller.id if seller else seller_user.id
    approved_products = Product.query.filter_by(seller_id=s_id, status='Approved').all()

    return jsonify({
        'seller': serialize_user(seller_user),
        'products': [serialize_product(p) for p in approved_products]
    }), 200

@api_bp.route('/seller/subscriptions', methods=['GET'])
@api_login_required
@api_role_required('seller')
def api_seller_subscriptions():
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        return jsonify({'error': 'Seller profile not found'}), 404

    active_subscriptions = Subscription.query.filter_by(status='active').all()
    subscribed_ids = [s.subscription_id for s in SellerSubscription.query.filter_by(seller_id=seller.id).all()]

    all_plans = [{
        'id': s.id,
        'name': s.name,
        'description': s.description,
        'price': s.price,
        'validity_period': s.validity_period,
        'features': s.features,
        'is_subscribed': s.id in subscribed_ids
    } for s in active_subscriptions]

    return jsonify({'subscriptions': all_plans}), 200

@api_bp.route('/seller/subscriptions/<int:subscription_id>/toggle', methods=['POST'])
@api_login_required
@api_role_required('seller')
def api_toggle_seller_subscription(subscription_id):
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        return jsonify({'error': 'Seller profile not found'}), 404

    subscription = Subscription.query.get_or_404(subscription_id)
    existing = SellerSubscription.query.filter_by(seller_id=seller.id, subscription_id=subscription.id).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'message': f'Unsubscribed from {subscription.name}', 'is_subscribed': False}), 200

    valid_until = datetime.utcnow() + timedelta(days=subscription.validity_period)
    new_sub = SellerSubscription(
        seller_id=seller.id,
        subscription_id=subscription.id,
        subscribed_on=datetime.utcnow(),
        valid_until=valid_until
    )
    db.session.add(new_sub)
    db.session.commit()

    return jsonify({'message': f'Subscribed to {subscription.name}', 'is_subscribed': True}), 200

@api_bp.route('/seller/commission-plans', methods=['GET', 'POST'])
@api_login_required
@api_role_required('seller')
def api_seller_commission_plans():
    seller = Seller.query.filter_by(user_id=current_user.id).first()
    if not seller:
        return jsonify({'error': 'Seller profile not found'}), 404

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        plan_name = data.get('plan_name', '').strip()
        commission_rate = data.get('commission_rate', type=float)
        description = data.get('description', '').strip()

        if not plan_name or commission_rate is None:
            return jsonify({'error': 'Plan name and commission rate are required'}), 400

        plan = CommissionPlan(
            plan_name=plan_name,
            commission_rate=commission_rate,
            description=description,
            seller_id=seller.id
        )
        db.session.add(plan)
        db.session.commit()
        return jsonify({'message': 'Commission plan created', 'plan_id': plan.id}), 201

    plans = CommissionPlan.query.filter_by(seller_id=seller.id).all()
    plans_data = [{
        'id': p.id,
        'plan_name': p.plan_name,
        'commission_rate': p.commission_rate,
        'description': p.description,
        'is_active': p.is_active
    } for p in plans]

    return jsonify({'commission_plans': plans_data}), 200

# ================= AFFILIATE ENDPOINTS =================

@api_bp.route('/affiliate/dashboard', methods=['GET'])
@api_login_required
def api_affiliate_dashboard():
    affiliate = current_user.affiliate_account
    if not affiliate:
        return jsonify({'is_affiliate': False, 'message': 'User is not an affiliate'}), 200

    referrals = Referral.query.filter_by(affiliate_id=affiliate.id).all()
    total_earnings = sum(r.commission for r in referrals if r.status == 'approved')
    pending_commissions = sum(r.commission for r in referrals if r.status == 'pending')

    referrals_data = [{
        'id': r.id,
        'product_id': r.product_id,
        'product_name': r.product.name if r.product else 'Unknown',
        'commission': r.commission,
        'timestamp': r.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        'status': r.status
    } for r in referrals]

    approved_products = Product.query.filter_by(status='Approved').limit(20).all()

    return jsonify({
        'is_affiliate': True,
        'referral_code': affiliate.referral_code,
        'total_earnings': total_earnings,
        'pending_commissions': pending_commissions,
        'total_referrals': len(referrals),
        'referrals': referrals_data,
        'promotable_products': [serialize_product(p) for p in approved_products]
    }), 200

@api_bp.route('/affiliate/generate-link/<int:product_id>', methods=['GET'])
@api_login_required
def api_generate_referral_link(product_id):
    affiliate = current_user.affiliate_account
    if not affiliate:
        return jsonify({'error': 'You are not an affiliate'}), 403

    product = Product.query.get_or_404(product_id)
    referral_link = f"/seller/product_details/{product.id}?ref={affiliate.referral_code}"

    new_referral = Referral(
        affiliate_id=affiliate.id,
        product_id=product.id,
        commission=0.0
    )
    db.session.add(new_referral)
    db.session.commit()

    return jsonify({
        'product_id': product.id,
        'referral_code': affiliate.referral_code,
        'referral_link': referral_link
    }), 200

# ================= MESSAGING ENDPOINTS =================

@api_bp.route('/chat/conversations', methods=['GET'])
@api_login_required
def api_get_conversations():
    conversations = Conversation.query.filter(
        (Conversation.buyer_id == current_user.id) | (Conversation.seller_id == current_user.id)
    ).order_by(Conversation.created_at.desc()).all()

    results = []
    for conv in conversations:
        other_user = conv.seller if conv.buyer_id == current_user.id else conv.buyer
        last_msg = Message.query.filter_by(conversation_id=conv.id).order_by(Message.timestamp.desc()).first()

        results.append({
            'conversation_id': conv.id,
            'product_id': conv.product_id,
            'product_name': conv.product.name if conv.product else 'Product',
            'other_user_id': other_user.id if other_user else None,
            'other_user_name': other_user.username if other_user else 'User',
            'last_message': last_msg.content if last_msg else '',
            'last_timestamp': last_msg.timestamp.strftime('%Y-%m-%d %H:%M:%S') if last_msg else None
        })

    return jsonify({'conversations': results}), 200

@api_bp.route('/chat/get-or-create', methods=['POST'])
@api_login_required
def api_get_or_create_conversation():
    data = request.get_json(silent=True) or request.form
    seller_user_id = data.get('seller_id', type=int)
    product_id = data.get('product_id', type=int)

    if not seller_user_id or not product_id:
        return jsonify({'error': 'seller_id and product_id are required'}), 400

    conv = Conversation.query.filter_by(
        buyer_id=current_user.id, seller_id=seller_user_id, product_id=product_id
    ).first()

    if not conv:
        conv = Conversation(
            buyer_id=current_user.id,
            seller_id=seller_user_id,
            product_id=product_id
        )
        db.session.add(conv)
        db.session.commit()

    return jsonify({'conversation_id': conv.id}), 200

@api_bp.route('/chat/<int:conversation_id>/messages', methods=['GET'])
@api_login_required
def api_get_messages(conversation_id):
    conv = Conversation.query.get_or_404(conversation_id)
    if conv.buyer_id != current_user.id and conv.seller_id != current_user.id and getattr(current_user, 'role', '') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403

    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp.asc()).all()

    return jsonify({
        'messages': [{
            'id': msg.id,
            'sender_id': msg.sender_id,
            'sender_name': msg.sender.username if msg.sender else 'User',
            'sender_role': msg.sender_role,
            'content': msg.content,
            'timestamp': msg.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'is_me': msg.sender_id == current_user.id
        } for msg in messages]
    }), 200

@api_bp.route('/chat/messages', methods=['POST'])
@api_login_required
def api_send_message():
    data = request.get_json(silent=True) or request.form
    conversation_id = data.get('conversation_id', type=int)
    content = data.get('content', '').strip()

    if not conversation_id or not content:
        return jsonify({'error': 'conversation_id and content are required'}), 400

    conv = Conversation.query.get_or_404(conversation_id)
    if conv.buyer_id != current_user.id and conv.seller_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403

    sender_role = 'buyer' if current_user.id == conv.buyer_id else 'seller'

    new_msg = Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        sender_role=sender_role,
        content=content
    )
    db.session.add(new_msg)
    db.session.commit()

    return jsonify({
        'message': 'Message sent successfully',
        'data': {
            'id': new_msg.id,
            'sender_id': current_user.id,
            'sender_name': current_user.username,
            'sender_role': sender_role,
            'content': content,
            'timestamp': new_msg.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'is_me': True
        }
    }), 201

# ================= ADMIN ENDPOINTS =================

@api_bp.route('/admin/dashboard', methods=['GET'])
@api_login_required
@api_role_required('admin')
def api_admin_dashboard():
    total_users = User.query.count()
    total_buyers = Buyer.query.count()
    total_sellers = Seller.query.count()
    pending_products = Product.query.filter_by(status='Pending').all()

    return jsonify({
        'total_users': total_users,
        'total_buyers': total_buyers,
        'total_sellers': total_sellers,
        'pending_products': [serialize_product(p) for p in pending_products]
    }), 200

@api_bp.route('/admin/users', methods=['GET'])
@api_login_required
@api_role_required('admin')
def api_admin_users():
    users = User.query.all()
    return jsonify({'users': [serialize_user(u) for u in users]}), 200

@api_bp.route('/admin/products/<int:product_id>/approve', methods=['POST'])
@api_login_required
@api_role_required('admin')
def api_approve_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.status = 'Approved'
    db.session.commit()
    return jsonify({'message': f'Product "{product.name}" approved successfully'}), 200

@api_bp.route('/admin/products/<int:product_id>/reject', methods=['POST'])
@api_login_required
@api_role_required('admin')
def api_reject_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.status = 'Rejected'
    db.session.commit()
    return jsonify({'message': f'Product "{product.name}" rejected'}), 200
