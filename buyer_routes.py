from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from flask import json, render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from werkzeug.utils import secure_filename
from models import Affiliate, AffiliateCommissionPlan, AffiliateSignup, Buyer, CommissionSettings, Delivery, DriverLocation, Product, Cart, ProductComponent, ProductImage, Order, OrderItem, ProductReview, SavedProduct, Conversation, User
from forms import AddToCartForm, CheckoutForm
from database import db
from functools import wraps
from fpdf import FPDF
from datetime import datetime
import os, requests, json

from wallet import credit_wallet, get_or_create_wallet


buyer_bp = Blueprint('buyer_routes', __name__, static_folder='static', static_url_path='/static')

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

@buyer_bp.route('/buyer_dashboard')
@login_required
@role_required('buyer')
def buyer_dashboard():
    # Fetch orders for the current buyer
    orders = db.session.query(Order, Product).join(OrderItem, OrderItem.order_id == Order.id)\
        .join(Product, Product.id == OrderItem.product_id).filter(Order.buyer_id == current_user.id).all()
    # Example: get the first conversation for the current user
    conversations = Conversation.query.filter_by(buyer_id=current_user.id).all()
    conversation_id = conversations[0].id if conversations else None

    return render_template(
        'buyer/buyer_dashboard.html',
        orders=orders,
        show_footer=False,
        conversation_id=conversation_id,
        active='dashboard'
    )


@buyer_bp.route('/product/<int:product_id>/review', methods=['POST'])
@login_required
def submit_product_review(product_id):
    product = Product.query.get_or_404(product_id)

    if current_user.role != 'buyer':
        flash("Only buyers can submit reviews.", "warning")
        return redirect(url_for('seller_bp.product_details', product_id=product.id))

    rating = int(request.form.get('rating', 0))
    comment = request.form.get('comment', '').strip()

    if rating < 1 or rating > 5:
        flash("Invalid rating.", "danger")
        return redirect(url_for('seller_bp.product_details', product_id=product.id))

    review = ProductReview.query.filter_by(product_id=product.id, buyer_id=current_user.id).first()
    if review:
        # update existing review
        review.rating = rating
        review.comment = comment
        review.date_created = datetime.utcnow()
        flash("Your review has been updated.", "success")
    else:
        # create new review
        review = ProductReview(product_id=product.id, buyer_id=current_user.id, rating=rating, comment=comment)
        db.session.add(review)
        flash("Your review has been submitted.", "success")

    db.session.commit()
    return redirect(url_for('seller_routes.product_details', product_id=product.id))

@buyer_bp.route("/notifications")
@login_required
def notifications():
    return render_template("buyer/notifications.html")

@buyer_bp.route('/faq')
def faq():
    return render_template('faq.html')  # Ensure you have the faq.html template in your templates folder

@buyer_bp.route('/cart', methods=['GET'])
@login_required
def cart():
    if current_user.role != 'buyer':
        flash("You are not authorized to view this page.", "danger")
        return redirect(url_for('routes.marketplace'))  # Adjusted endpoint

    # Fetch cart items
    cart_items = Cart.query.filter_by(user_id=current_user.id).all()
    
    # Calculate total amount
    total_amount = sum(
        item.product.price * item.quantity if item.product else 0
        for item in cart_items
    )
    
    # Pass data to the template
    return render_template('buyer/cart.html', cart_items=cart_items, total_amount=total_amount)

@buyer_bp.route('/add_to_cart/<int:product_id>', methods=['POST'])
@login_required
def add_to_cart(product_id):
    product = Product.query.get(product_id)
    if not product:
        flash("Product not found!", "error")
        return redirect(url_for('routes.marketplace'))

    # Get and log the quantity value
    quantity = request.form.get('quantity', 1)
    print(f"Quantity received: {quantity}")  # Add a debug log

    try:
        quantity = int(quantity)
        if quantity < 1:
            raise ValueError("Quantity must be at least 1.")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for('buyer_routes.cart'))

    cart_item = Cart.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if cart_item:
        cart_item.quantity += quantity  # Add the new quantity to the existing quantity
    else:
        cart_item = Cart(user_id=current_user.id, product_id=product_id, quantity=quantity)
        db.session.add(cart_item)

    db.session.commit()
    flash("Product added to cart!", "success")
    return redirect(url_for('buyer_routes.cart'))

@buyer_bp.route('/update_cart', methods=['POST'])
@login_required
def update_cart():
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    item_id = data.get('item_id')
    qty = data.get('quantity')
    if action == 'remove' and item_id:
        ci = Cart.query.filter_by(id=item_id, user_id=current_user.id).first()
        if ci:
            db.session.delete(ci); db.session.commit()
        return jsonify(success=True)
    if action == 'update' and item_id:
        ci = Cart.query.filter_by(id=item_id, user_id=current_user.id).first()
        if ci:
            ci.quantity = int(qty or 1); db.session.commit()
        return jsonify(success=True)
    return jsonify(success=False), 400

@buyer_bp.route('/remove_from_cart/<int:cart_id>', methods=['POST'])
@login_required
@role_required('buyer')
def remove_from_cart(cart_id):
    cart_item = Cart.query.get(cart_id)
    if cart_item and cart_item.user_id == current_user.id:
        db.session.delete(cart_item)
        db.session.commit()
        flash('Item removed from cart.', 'success')
    else:
        flash('Item not found or access denied.', 'danger')
    return redirect(url_for('buyer_routes.cart'))


@buyer_bp.route('/clear_cart', methods=['POST'])
@login_required
@role_required('buyer')
def clear_cart():
    Cart.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash('All items have been removed from the cart.', 'success')
    return redirect(url_for('buyer_routes.cart'))

@buyer_bp.route('/place_order', methods=['POST'])
def place_order():
    # Get cart items for the current user
    cart_items = Cart.query.filter_by(user_id=current_user.id).all()

    if not cart_items:
        return jsonify({"error": "Your cart is empty"}), 400

    # Calculate total amount and create order items
    total_amount = 0
    order_items = []
    for item in cart_items:
        product = item.product
        if product:
            total_price = product.price * item.quantity
            total_amount += total_price

            order_item = OrderItem(
                product_id=product.id,
                quantity=item.quantity,
                total_price=total_price
            )
            order_items.append(order_item)

    # Save order in database
    new_order = Order(
        buyer_id=current_user.id,
        total_amount=total_amount
    )
    db.session.add(new_order)
    db.session.commit()

    # Add order items to the order
    for order_item in order_items:
        order_item.order_id = new_order.id
        db.session.add(order_item)

    db.session.commit()

    # Clear the cart after placing the order
    for item in cart_items:
        db.session.delete(item)
    db.session.commit()

    return redirect(url_for('buyer_routes.cart'))

@buyer_bp.route('/my_orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.id.desc()).all()

    # Check if endpoints exist
    reorder_enabled = any(rule.endpoint == 'buyer_routes.reorder' for rule in current_app.url_map.iter_rules())
    contact_enabled = any(rule.endpoint == 'routes.contact_seller' for rule in current_app.url_map.iter_rules())
    details_enabled = any(rule.endpoint == 'buyer_routes.order_detail' for rule in current_app.url_map.iter_rules())

    return render_template(
        'buyer/my_orders.html',
        orders=orders,
        active="orders",
        reorder_enabled=reorder_enabled,
        contact_enabled=contact_enabled,
        details_enabled=details_enabled
    )

def calculate_delivery_cost(total_amount):
    base = Decimal('0.0')  # or any fixed base delivery fee
    pct = Decimal('0.05') * total_amount
    return (base + pct).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@buyer_bp.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if current_user.role != 'buyer':
        flash("You are not authorized to access this page.", "danger")
        return redirect(url_for('routes.marketplace'))

    cart_items = Cart.query.filter_by(user_id=current_user.id).all()
    if not cart_items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for('routes.marketplace'))

    # compute subtotal
    subtotal_amount = Decimal('0.00')
    for ci in cart_items:
        if ci.product:
            price = Decimal(ci.product.price or 0)
            qty = Decimal(ci.quantity or 1)
            subtotal_amount += (price * qty)
    subtotal_amount = subtotal_amount.quantize(Decimal('0.01'), rounding=ROUND_DOWN)

    # Defaults
    buyer_discount_pct = Decimal('0.00')
    affiliate_commission_pct = Decimal('0.00')
    affiliate = None
    affiliate_plan = None

    # Find affiliate signup record for this buyer (if any)
    affiliate_signup = AffiliateSignup.query.filter_by(user_id=current_user.id).first()
    if affiliate_signup:
        affiliate = Affiliate.query.get(affiliate_signup.affiliate_id)
        if affiliate:
            settings = CommissionSettings.query.order_by(CommissionSettings.id.desc()).first()

            # Buyer discount: admin override or affiliate plan
            if settings and settings.admin_control_enabled and settings.affiliate_buyer_discount_percent is not None:
                buyer_discount_pct = Decimal(settings.affiliate_buyer_discount_percent)
            else:
                affiliate_plan = AffiliateCommissionPlan.query.filter_by(affiliate_id=affiliate.id, is_active=True).order_by(AffiliateCommissionPlan.date_created.desc()).first()
                if affiliate_plan:
                    buyer_discount_pct = Decimal(affiliate_plan.buyer_discount or 0)

            # Affiliate commission: admin override or affiliate plan
            if settings and settings.admin_control_enabled and settings.global_affiliate_percent is not None:
                affiliate_commission_pct = Decimal(settings.global_affiliate_percent)
            else:
                affiliate_commission_pct = Decimal(affiliate_plan.commission_percent if affiliate_plan else 0)

    if request.method == 'POST':
        # gather form data
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        latitude = request.form.get('latitude', '').strip()
        longitude = request.form.get('longitude', '').strip()
        payment_method = request.form.get('payment_method', '').strip()
        delivery_option = request.form.get('delivery_option', 'no')

        # validate
        if not name:
            flash("Please provide your name.", "danger")
            return redirect(url_for('buyer_routes.checkout'))
        if delivery_option == 'yes' and (not address or not latitude or not longitude):
            flash("Please provide a delivery location on the map.", "danger")
            return redirect(url_for('buyer_routes.checkout'))

        try:
            # compute discount & totals
            buyer_discount_amount = (subtotal_amount * (buyer_discount_pct / Decimal('100.0'))) if buyer_discount_pct else Decimal('0.00')
            buyer_discount_amount = buyer_discount_amount.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            total_after_discount = (subtotal_amount - buyer_discount_amount).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

            # Create ONE order and attach affiliate fields
            order = Order(
                buyer_id=current_user.id,
                total_amount=total_after_discount,
                order_date=datetime.utcnow(),
                status='Pending',
                shipping_name=name,
                shipping_address=address if address else None,
                latitude=float(latitude) if latitude else None,
                longitude=float(longitude) if longitude else None,
                payment_method=payment_method or None,
                affiliate_id=(affiliate.id if affiliate else None),
                affiliate_commission_amount=None,
                buyer_discount_amount=buyer_discount_amount
            )
            db.session.add(order)
            db.session.flush()  # order.id available

            # create order items
            for ci in cart_items:
                if not ci.product:
                    continue
                item_price = Decimal(ci.product.price or 0).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
                qty = int(ci.quantity or 1)
                db.session.add(OrderItem(
                    order_id=order.id,
                    product_id=ci.product.id,
                    quantity=qty,
                    total_price=(item_price * qty),
                    status='Pending'
                ))

            # create affiliate commission pending txn if applicable
            if affiliate and affiliate_commission_pct and affiliate_commission_pct > 0:
                commission_amount = (total_after_discount * (affiliate_commission_pct / Decimal('100.0'))).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
                order.affiliate_commission_amount = commission_amount

                # ensure affiliate wallet
                affiliate_wallet = get_or_create_wallet(affiliate.user_id, currency=order.buyer_id and User.query.get(order.buyer_id).country or None)

                affiliate_txn = credit_wallet(
                    affiliate_wallet,
                    commission_amount,
                    source='affiliate_commission',
                    reference_type='order',
                    reference_id=order.id,
                    description=f'Affiliate commission for order {order.id}',
                    status='pending' if (settings and settings.admin_control_enabled) else 'completed'
                )
                # optional: store txn id on order somewhere if you have the column (for trace)
                # e.g. order.affiliate_commission_txn_id = affiliate_txn.id

            # Delivery creation (if requested)
            if delivery_option == 'yes':
                # pick first seller location as pickup
                seller_locations = [ci.product.seller.location for ci in cart_items if ci.product and ci.product.seller and ci.product.seller.location]
                seller_coords = [(ci.product.seller.lat, ci.product.seller.lng) for ci in cart_items if ci.product and ci.product.seller and ci.product.seller.lat is not None and ci.product.seller.lng is not None]
                pickup_location = seller_locations[0] if seller_locations else current_app.config.get('WAREHOUSE_ADDRESS', 'Marketplace Warehouse')
                pickup_lat, pickup_lng = seller_coords[0] if seller_coords else (None, None)

                delivery = Delivery(
                    order_id=order.id,
                    buyer_id=current_user.id,
                    driver_id=None,
                    pickup_location=pickup_location,
                    dropoff_location=address,
                    pickup_lat=pickup_lat,
                    pickup_lng=pickup_lng,
                    dropoff_lat=float(latitude) if latitude else None,
                    dropoff_lng=float(longitude) if longitude else None,
                    distance_km=None,
                    estimated_cost=calculate_delivery_cost(total_after_discount),
                    status="Pending",
                    created_at=datetime.utcnow()
                )
                db.session.add(delivery)

            # clear cart and commit once
            Cart.query.filter_by(user_id=current_user.id).delete()
            db.session.commit()

            session['recent_order_id'] = order.id
            session['recent_order_total'] = total_after_discount

            flash("✅ Order placed successfully.", "success")
            return redirect(url_for('buyer_routes.checkout_confirmation'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("checkout: failed to create order: %s", e)
            flash("An error occurred while placing your order. Please try again.", "danger")
            return redirect(url_for('buyer_routes.checkout'))

    # GET: render checkout page
    towns_path = os.path.join(current_app.static_folder, "data", "ghana_towns.json")
    with open(towns_path, "r", encoding="utf-8") as f:
        towns = json.load(f)

    return render_template(
        'buyer/checkout.html',
        cart_items=cart_items,
        subtotal_amount=subtotal_amount,
        buyer_discount_pct=buyer_discount_pct,
        buyer_discount_amount=(subtotal_amount * (buyer_discount_pct / Decimal('100.0'))).quantize(Decimal('0.01'), rounding=ROUND_DOWN) if buyer_discount_pct else Decimal('0.00'),
        total_amount=(subtotal_amount - (subtotal_amount * (buyer_discount_pct / Decimal('100.0'))) if buyer_discount_pct else subtotal_amount).quantize(Decimal('0.01'), rounding=ROUND_DOWN),
        other_locations=towns,
        Decimal=Decimal
    )


@buyer_bp.route('/checkout_confirmation')
def checkout_confirmation():
    # Get the checkout details from the session
    cart_items = session.get('cart_items', [])
    total_amount = session.get('total_amount', 0)
    name = session.get('name', '')
    address = session.get('address', '')
    payment = session.get('payment', '')

    # If session data is missing, redirect to checkout page
    if not cart_items or not name or not address or not payment:
        flash("No checkout details found, please try again.", "danger")
        return redirect(url_for('buyer_routes.checkout'))

    return render_template('buyer/checkout_confirmation.html', 
                           cart_items=cart_items, 
                           total_amount=total_amount,
                           name=name, 
                           address=address, 
                           payment=payment)

# buyer_routes.py or seller_routes.py
@buyer_bp.route("/track")
@login_required
def track_delivery():
    # Get the buyer’s currently active delivery (if any)
    delivery = Delivery.query.filter_by(
        buyer_id=current_user.id, status='active'
    ).first()

    return render_template("buyer/track_delivery.html", delivery=delivery)

@buyer_bp.route("/driver/location/<int:driver_id>")
@login_required
def get_driver_location(driver_id):
    loc = DriverLocation.query.filter_by(driver_id=driver_id).first()
    if not loc:
        return jsonify({"status": "error", "msg": "No location yet"}), 404

    return jsonify({
        "lat": loc.lat,
        "lng": loc.lng,
        "updated_at": loc.updated_at.isoformat()
    })

@buyer_bp.route('/save_receipt/<filename>')
def save_receipt(filename):
    receipt_path = os.path.join(RECEIPTS_FOLDER, filename)
    return send_file(receipt_path, as_attachment=True)

@buyer_bp.route('/print_receipt/<filename>')
def print_receipt(filename):
    receipt_path = os.path.join(RECEIPTS_FOLDER, filename)
    return send_file(receipt_path)

@buyer_bp.route('/payment_gateway', methods=['POST'])
@login_required
def payment_gateway():
    payment_method = request.form.get('payment_method')
    
    if payment_method == 'card':
        card_number = request.form.get('card_number')
        expiry_date = request.form.get('expiry_date')
        cvv = request.form.get('cvv')
        # Process card payment logic here
        return f"Processed card payment with card number ending in {card_number[-4:]}"
    
    elif payment_method == 'paypal':
        paypal_email = request.form.get('paypal_email')
        # Process PayPal payment logic here
        return f"Processed PayPal payment for email {paypal_email}"
    
    elif payment_method == 'bank_transfer':
        account_number = request.form.get('account_number')
        bank_name = request.form.get('bank_name')
        ifsc_code = request.form.get('ifsc_code')
        # Process bank transfer logic here
        return f"Processed bank transfer for account {account_number} at {bank_name}"
    
    return "Invalid payment method selected!"


# Buyer Chat Route
@buyer_bp.route('/chat')
@login_required
def buyer_chat():
    return render_template('chat_page.html', user=current_user)
