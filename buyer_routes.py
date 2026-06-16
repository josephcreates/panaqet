from flask import render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from werkzeug.utils import secure_filename
from models import Buyer, Seller, Product, Cart, ProductComponent, ProductImage, Order, OrderItem, SavedProduct, Conversation
from forms import AddToCartForm, CheckoutForm
from database import db
from functools import wraps
from fpdf import FPDF
from datetime import datetime
import os


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

    return render_template('buyer_dashboard.html', orders=orders, show_footer=False)


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
    return render_template('cart.html', cart_items=cart_items, total_amount=total_amount)

# Save product to wishlist
@buyer_bp.route('/wishlist/save/<int:product_id>', methods=['POST'])
@login_required
def save_to_wishlist(product_id):
    # Ensure only buyers can save products
    if current_user.role != 'buyer':
        return jsonify({'message': 'Only buyers can save products to the wishlist'}), 403

    # Check if product is already in wishlist
    saved_item = SavedProduct.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if saved_item:
        return jsonify({'message': 'Product is already in your wishlist'}), 400

    # Save product
    new_saved_item = SavedProduct(user_id=current_user.id, product_id=product_id, date_saved=datetime.utcnow())
    db.session.add(new_saved_item)
    db.session.commit()

    return jsonify({'message': 'Product added to your wishlist successfully'}), 200

# Retrieve all products in the wishlist
@buyer_bp.route('/wishlist', methods=['GET'])
@login_required
def get_wishlist():
    # Ensure only buyers can view their wishlist
    if current_user.role != 'buyer':
        return jsonify({'message': 'Only buyers can view their wishlist'}), 403

    # Retrieve all saved products for the current user
    saved_items = SavedProduct.query.filter_by(user_id=current_user.id).all()

    # Format the wishlist data
    wishlist = [{
        'id': item.product.id,
        'name': item.product.name,
        'description': item.product.description,
        'price': item.product.price,
        'date_saved': item.date_saved
    } for item in saved_items]

    return jsonify(wishlist), 200

# Remove product from wishlist
@buyer_bp.route('/wishlist/remove/<int:product_id>', methods=['POST'])
@login_required
def remove_from_wishlist(product_id):
    # Find the product in the wishlist
    saved_item = SavedProduct.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if not saved_item:
        return jsonify({'message': 'Product not found in your wishlist'}), 404

    # Remove the product
    db.session.delete(saved_item)
    db.session.commit()

    return jsonify({'message': 'Product removed from your wishlist successfully'}), 200

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

@buyer_bp.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if current_user.role != 'buyer':
        flash("You are not authorized to view this page.", "danger")
        return redirect(url_for('routes.marketplace'))

    # Fetch cart items from the database for the logged-in user
    cart_items = Cart.query.filter_by(user_id=current_user.id).all()

    # Calculate the total cart amount
    total_amount = sum(
        item.product.price * item.quantity if item.product else 0
        for item in cart_items
    )

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        payment = request.form.get('payment', '').strip()
        latitude = request.form.get('latitude', '').strip()
        longitude = request.form.get('longitude', '').strip()

        # Validate form data
        if not name or not address or not payment:
            flash("Please fill in all required fields.", "danger")
            return redirect(url_for('buyer_routes.checkout'))

        if not latitude or not longitude:
            flash("Unable to capture location data. Please enable location services.", "danger")
            return redirect(url_for('buyer_routes.checkout'))

        db.session.commit()

        flash("Order placed successfully!", "success")
        return redirect(url_for('buyer_routes.checkout_confirmation'))

    return render_template('checkout.html', cart_items=cart_items, total_amount=total_amount)

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

    return render_template('checkout_confirmation.html', 
                           cart_items=cart_items, 
                           total_amount=total_amount,
                           name=name, 
                           address=address, 
                           payment=payment)

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
