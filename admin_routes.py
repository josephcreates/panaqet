from decimal import Decimal
from flask import render_template, redirect, url_for, flash, request, session, abort, Blueprint, send_file, send_from_directory, current_app, make_response, Response, jsonify
from flask_login import login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from sqlalchemy import func
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
import pandas as pd
from openpyxl import Workbook
from io import BytesIO
from forms import AdminLoginForm, ApproveDriverForm, EditProfileForm, ProductForm, AddToCartForm, SettingsForm, CommissionPlanForm
from models import Admin, Category, CommissionSettings, Conversation, Driver, SystemConfig, User, Buyer, Seller, Product, Cart, ProductComponent, ProductImage, Order, Subscription, CommissionPlan, Wallet, WalletTransaction
from database import db
from functools import wraps
from fpdf import FPDF
from docx import Document
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import letter
import io
import qrcode
from qrcode.constants import ERROR_CORRECT_H
import os

from wallet import settle_transaction

admin_bp = Blueprint('admin_routes', __name__, static_folder='static', static_url_path='/static')

#----------------FUNCTIONS DECORATOR-------------------
# Custom decorator for role-based access control
def role_required(*roles):
    def wrapper(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('admin_bp.admin_login'))
            
            if not hasattr(current_user, "role") or current_user.role not in roles:
                flash('Access denied. You do not have permission to access this page.', 'danger')
                return redirect(url_for('routes.index'))
            
            return func(*args, **kwargs)
        return decorated_view
    return wrapper


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

#---------ADMIN-----------
@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    form = AdminLoginForm()
    if form.validate_on_submit():
        admin = Admin.query.filter_by(email=form.email.data).first()
        if admin and admin.check_password(form.password.data):
            session['user_type'] = 'admin'               # set BEFORE login_user
            login_user(admin, remember=form.remember.data)
            flash('Admin login successful.', 'success')
            return redirect(url_for('admin_routes.admin_dashboard'))   # always go to admin dashboard
        flash('Invalid email or password.', 'danger')
    return render_template('admin/admin_login.html', title='Admin Login', form=form)

# Update the admin_dashboard route
@admin_bp.route('/dashboard')
@login_required
@role_required('admin')
def admin_dashboard():
    total_users = User.query.count()
    total_buyers = Buyer.query.count()
    total_sellers = Seller.query.count()
    total_admins = Admin.query.count()
    products = Product.query.filter_by(status='Pending').all()
    users = User.query.all()  # ✅ fetch all users

    return render_template(
        'admin/admin_dashboard.html',
        total_users=total_users,
        total_buyers=total_buyers,
        total_sellers=total_sellers,
        total_admins=total_admins,
        products=products,
        users=users  # ✅ pass to template
    )

@admin_bp.route('/admin/conversations')
@login_required
def view_conversations():
    """
    View all conversations in the database with buyer, seller, product, last message.
    """
    conversations = Conversation.query.order_by(Conversation.id.desc()).all()
    
    convo_data = []
    for c in conversations:
        last_msg = c.messages[-1] if c.messages else None
        convo_data.append({
            "id": c.id,
            "buyer": c.buyer.username if c.buyer else "Unknown",
            "seller": c.seller.username if c.seller else "Unknown",
            "product": c.product.name if c.product else "N/A",
            "created_at": c.created_at,
            "updated_at": c.updated_at if hasattr(c,'updated_at') else None,
            "last_message": last_msg.content if last_msg else "No messages",
            "last_message_time": last_msg.timestamp if last_msg else None
        })

    return render_template("admin/view_conversations.html", conversations=convo_data)

@admin_bp.route('/user_management')
@login_required
@role_required('admin')
def user_management():
    users = User.query.all()
    return render_template('admin/user_management.html', users=users)

@admin_bp.route('/approve_driver/<int:driver_id>', methods=['POST'])
@login_required
@role_required('admin')
def approve_driver(driver_id):
    driver = Driver.query.get_or_404(driver_id)
    driver.status = 'Approved'
    db.session.commit()
    flash(f"{driver.full_name} has been approved successfully.", "success")
    return redirect(url_for('admin_routes.driver_management'))

@admin_bp.route('/driver_management')
@login_required
def driver_management():
    drivers = Driver.query.all()
    approve_form = ApproveDriverForm()
    return render_template('admin/driver_management.html', drivers=drivers, approve_form=approve_form)

@admin_bp.route('/view_user/<int:user_id>', methods=['GET'])
@login_required
def admin_view_user(user_id):
    user = User.query.get_or_404(user_id)
    # Ensure profile image URL uses forward slashes
    profile_image_url = user.profile_image.replace('\\', '/')    
    current_app.logger.debug(f"Profile image path in admin view: {profile_image_url}")
    return render_template('admin/view_user.html', user=user, profile_image_url=profile_image_url)

@admin_bp.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = EditProfileForm()  # Instantiate the form

    if request.method == 'POST':
        if form.validate_on_submit():
            # Logic for editing user details, e.g., saving the image if uploaded
            user.username = form.username.data
            user.email = form.email.data
            
            # Check for the uploaded profile image
            if form.profile_image.data:
                # Save the image logic here
                file = form.profile_image.data
                filename = secure_filename(file.filename)
                file.save(os.path.join(admin_bp.root_path, 'static/users', f"{user.username}_{user.id}/profile_images", filename))
                user.profile_image = f"users/{user.username}_{user.id}/profile_images/{filename}"

            db.session.commit()  # Commit changes to the database
            return redirect(url_for('admin_routes.admin_dashboard'))

    # Populate the form with existing user data
    form.username.data = user.username
    form.email.data = user.email

    return render_template('edit_user.html', form=form, user=user)

@admin_bp.route('/delete_user/<int:user_id>', methods=['GET', 'POST'])
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        # Redirect to confirm delete page
        return redirect(url_for('routes.admin_confirm_delete_user', user_id=user.id))
    return render_template('delete_user.html', user=user)

@admin_bp.route('/confirm_delete_user/<int:user_id>', methods=['POST'])
def admin_confirm_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash('User has been deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error deleting user: ' + str(e), 'danger')
    return redirect(url_for('routes.admin_dashboard'))

@admin_bp.route('/preview_users_pdf')
def preview_users_pdf():
    # Create a BytesIO buffer
    buffer = BytesIO()    
    # Create a PDF canvas
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    # Set title
    p.setFont("Helvetica-Bold", 16)
    p.drawString(200, height - 50, "User List")
    # Set column titles
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, height - 100, "ID")
    p.drawString(150, height - 100, "Username")
    p.drawString(300, height - 100, "Email")
    p.drawString(450, height - 100, "Role")
    # Add a line for the header
    p.line(50, height - 105, 550, height - 105)
    # Reset font for data
    p.setFont("Helvetica", 12)
    # Sample data retrieval, replace with your actual database call
    users = User.query.all()  # Fetch all users from the database
    # Print user data in the table
    y = height - 120  # Starting y position for user data
    for user in users:
        p.drawString(50, y, str(user.id))
        p.drawString(150, y, user.username)
        p.drawString(300, y, user.email)
        p.drawString(450, y, user.role)
        y -= 20  # Move down for the next row
    # Finalize the PDF
    p.showPage()
    p.save()    
    # Move the buffer cursor to the beginning
    buffer.seek(0)
    # Return the PDF as a response to display in the browser
    return Response(buffer, mimetype='application/pdf')

@admin_bp.route('/export_users_pdf')
def export_users_pdf():
    # Your PDF generation code
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    # Add content to PDF
    p.showPage()
    p.save()
    buffer.seek(0)
    return Response(buffer, mimetype='application/pdf',
                    headers={"Content-Disposition": "attachment;filename=users.pdf"})

@admin_bp.route('/export_users_excel')
@login_required
@role_required('admin')
def export_users_excel():
    # Assuming you have a list of users fetched from the database
    users = User.query.filter(User.role != 'admin').all()
    # Create a DataFrame
    data = {
        'ID': [user.id for user in users],
        'Username': [user.username for user in users],
        'Email': [user.email for user in users],
        'Role': [user.role for user in users]
    }
    df = pd.DataFrame(data)
    # Create an output buffer
    output = io.BytesIO()
    # Using 'openpyxl' to create an Excel file
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Users')
    output.seek(0)  # Go to the beginning of the BytesIO buffer
    # Use 'download_name' instead of 'attachment_filename'
    return send_file(output, download_name='users.xlsx', as_attachment=True)

@admin_bp.route('/export_users_word')
@login_required
@role_required('admin')
def export_users_word():
    # Fetch users from the database
    users = User.query.filter(User.role != 'admin').all()
    # Create a Word Document
    doc = Document()
    doc.add_heading('User List', 0)
    # Add a table
    table = doc.add_table(rows=1, cols=4)
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'ID'
    hdr_cells[1].text = 'Username'
    hdr_cells[2].text = 'Email'
    hdr_cells[3].text = 'Role'
    # Populate the table with user data
    for user in users:
        row_cells = table.add_row().cells
        row_cells[0].text = str(user.id)
        row_cells[1].text = user.username
        row_cells[2].text = user.email
        row_cells[3].text = user.role
    # Save to a BytesIO object
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)  # Move to the beginning of the BytesIO object
    # Use 'download_name' instead of 'attachment_filename'
    return send_file(output, download_name='users.docx', as_attachment=True)

@admin_bp.route('/products')
@login_required
@role_required('admin')
def admin_products():
    products = Product.query.all()
    return render_template('admin/admin_products.html', products=products)

@admin_bp.route('/view_product/<int:product_id>', methods=['GET'])
def admin_view_product(product_id):
    # Query the product by ID
    product = Product.query.get_or_404(product_id)    
    # Render the product details template with the product information
    return render_template('admin/admin_view_product.html', product=product)

# Define the folder for saving QR codes
QRCODE_FOLDER = 'static/qr_codes'
if not os.path.exists(QRCODE_FOLDER):
    os.makedirs(QRCODE_FOLDER)

@admin_bp.route('/product-management', methods=['GET'])
@login_required
def product_management():
    if current_user.role != 'admin':
        flash('Unauthorized access', 'danger')
        return redirect(url_for('routes.index'))

    products = Product.query.order_by(Product.date_added.desc()).all()
    return render_template('admin/product_management.html', products=products)

@admin_bp.route('/approve_product/<int:product_id>', methods=['POST'])
@login_required
def approve_product(product_id):
    if current_user.role != 'admin':
        flash('Unauthorized access', 'danger')
        return redirect(url_for('admin_routes.admin_dashboard'))
    
    product = Product.query.get_or_404(product_id)
    product.status = 'Approved'

    # Generate QR Code with version 2
    qr_data = f"Product ID: {product.id}, Product Name: {product.name}"
    qr = qrcode.QRCode(
        version=2,  # Specifies QR Code version
        error_correction=ERROR_CORRECT_H,  # High error correction level
        box_size=10,
        border=4,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)

    # Save the QR code to a file
    filename = f"product_{product.id}_qr.png"
    qr_image_path = os.path.join(QRCODE_FOLDER, filename)
    try:
        # Save QR Code to a file
        img = qr.make_image(fill_color="black", back_color="white")
        with open(qr_image_path, 'wb') as img_file:
            img.save(img_file)
        
        # Store public QR path in the database
        public_qr_path = url_for('static', filename=f'qr_codes/{filename}')
        product.qr_code = public_qr_path
        db.session.commit()

        flash(f'Product "{product.name}" has been approved and QR code generated.', 'success')
    except Exception as e:
        flash(f'Failed to generate QR code: {str(e)}', 'danger')

    return redirect(url_for('admin_routes.admin_dashboard'))

@admin_bp.route('/reject_product/<int:product_id>', methods=['POST'])
@login_required
def reject_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.status = 'Rejected'
    db.session.commit()
    flash('Product rejected.', 'success')
    return redirect(url_for('routes.admin_dashboard'))

@admin_bp.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    user = current_user
    # Check if the user has the necessary permissions
    if user.role == 'admin' or (user.role == 'seller' and user.id == product.seller_id):
        form = ProductForm()
        if request.method == 'POST':
            form = ProductForm(request.form, obj=product)
            if form.validate_on_submit():
                form.populate_obj(product)
                if form.image.data:
                    image_file = form.image.data
                    if allowed_file(image_file.filename):
                        user_folder = os.path.join('static', 'users', f"{current_user.username}_{current_user.id}", 'product_images')
                        os.makedirs(user_folder, exist_ok=True)
                        filename = secure_filename(image_file.filename)
                        file_path = os.path.join(user_folder, filename)
                        image_file.save(file_path)
                        product.image_url = os.path.join(f'users/{current_user.username}_{current_user.id}/product_images', filename)
                db.session.commit()
                flash('Product updated successfully!', 'success')
                return redirect(url_for('routes.admin_products') if user.role == 'admin' else url_for('seller_dashboard'))
        elif request.method == 'GET':
            form.name.data = product.name
            form.description.data = product.description
            form.price.data = product.price
            form.category.data = product.category
        return render_template('admin/edit_product.html', form=form, product=product)
    else:
        flash('You do not have permission to edit this product.', 'danger')
        return redirect(url_for('routes.products'))

@admin_bp.route('/products/delete/<int:product_id>', methods=['POST'])
@login_required
def admin_delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    user = current_user    
    if user.role == 'admin' or (user.role == 'seller' and user.id == product.seller_id):
        db.session.delete(product)
        db.session.commit()
        flash('Product deleted successfully!', 'success')
    else:
        flash('You do not have permission to delete this product.', 'danger')
    return redirect(url_for('routes.admin_products') if user.role == 'admin' else url_for('seller_dashboard'))

#-------------------COMMISSION--------------
@admin_bp.route('/commission-settings', methods=['GET','POST'])
@login_required
def commission_settings():
    settings = CommissionSettings.query.order_by(CommissionSettings.id.desc()).first()
    if not settings:
        settings = CommissionSettings()
    if request.method == 'POST':
        try:
            settings.global_affiliate_percent = float(request.form.get('global_affiliate_percent') or None) if request.form.get('global_affiliate_percent') else None
            settings.affiliate_buyer_discount_percent = float(request.form.get('affiliate_buyer_discount_percent') or None) if request.form.get('affiliate_buyer_discount_percent') else None
            settings.affiliate_signup_reward = float(request.form.get('affiliate_signup_reward') or None) if request.form.get('affiliate_signup_reward') else None
            settings.admin_control_enabled = bool(request.form.get('admin_control_enabled'))
            db.session.add(settings)
            db.session.commit()
            flash('Commission settings updated', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Could not update settings: ' + str(e), 'danger')
        return redirect(url_for('admin_routes.commission_settings'))

    return render_template('admin/commission_settings.html', settings=settings)

#----------------CREDIT/DEBIT WALLET & SETTLE AFFILIATE COMMISSION-------------------
def settle_affiliate_commission_for_order(order_id):
    order = Order.query.get(order_id)
    if not order or not order.affiliate_commission_amount:
        current_app.logger.info("settle_affiliate_commission_for_order: no commission for order %s", order_id)
        return False

    txn = WalletTransaction.query.filter_by(
        reference_type='order',
        reference_id=order.id,
        source='affiliate_commission',
        status='pending'
    ).first()
    if not txn:
        current_app.logger.info("settle_affiliate_commission_for_order: no pending txn for order %s", order_id)
        return False

    return settle_transaction(txn.id)


@admin_bp.route('/orders/<int:order_id>/mark_completed', methods=['POST'])
@login_required
def admin_mark_order_completed(order_id):
    if not current_user.is_admin:
        abort(403)
    order = Order.query.get_or_404(order_id)
    if order.status == 'Completed':
        flash('Order already completed.', 'info')
        return redirect(url_for('admin_routes.orders'))
    order.status = 'Completed'
    db.session.add(order)
    db.session.commit()

    # settle affiliate commission if present
    settled = settle_affiliate_commission_for_order(order_id)
    if settled:
        flash('Order completed and affiliate commission settled (if any).', 'success')
    else:
        flash('Order completed. No affiliate commission to settle or still pending admin approval.', 'success')

    return redirect(url_for('admin_routes.orders'))

#----------------CATEGORIES-------------------
def admin_required():
    # simple role check helper — replace with your decorator if you have one
    if not current_user.is_authenticated or getattr(current_user, "role", "") not in ("admin", "superadmin"):
        flash("Access denied", "danger")
        return False
    return True


def get_category_tree(parent_id=None):
    """Recursive function to get categories and children"""
    cats = Category.query.filter_by(parent_id=parent_id).order_by(Category.name).all()
    result = []
    for c in cats:
        result.append({
            "category": c,
            "children": get_category_tree(c.id)
        })
    return result

@admin_bp.route("/categories")
@login_required
def categories():
    category_tree = get_category_tree()
    return render_template("admin/categories.html", category_tree=category_tree)

@admin_bp.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    name = (request.form.get("name") or "").strip()
    parent_id = request.form.get("parent_id") or None

    if not name:
        flash("Category name is required.", "danger")
        return redirect(url_for("admin_routes.categories"))

    parent_id = int(parent_id) if parent_id else None
    # Prevent duplicates under the same parent
    duplicate = Category.query.filter_by(name=name, parent_id=parent_id).first()
    if duplicate:
        flash("Category with this name already exists at this level.", "warning")
        return redirect(url_for("admin_routes.categories"))

    new_cat = Category(name=name, parent_id=parent_id)
    db.session.add(new_cat)
    db.session.commit()
    flash("Category added successfully.", "success")
    return redirect(url_for("admin_routes.categories"))

@admin_bp.route("/categories/<int:id>/edit", methods=["POST"])
@login_required
def edit_category(id):
    cat = Category.query.get_or_404(id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Category name is required.", "danger")
        return redirect(url_for("admin_routes.categories"))

    # Check duplicates
    duplicate = Category.query.filter(Category.id != id, Category.name == name, Category.parent_id == cat.parent_id).first()
    if duplicate:
        flash("Another category with that name exists at this level.", "warning")
        return redirect(url_for("admin_routes.categories"))

    cat.name = name
    db.session.commit()
    flash("Category updated successfully.", "success")
    return redirect(url_for("admin_routes.categories"))

@admin_bp.route("/categories/<int:id>/delete", methods=["POST"])
@login_required
def delete_category(id):
    cat = Category.query.get_or_404(id)
    db.session.delete(cat)
    db.session.commit()
    flash("Category deleted successfully.", "success")
    return redirect(url_for("admin_routes.categories"))

#--------------ORDERS -------------------
@admin_bp.route('/orders', methods=['GET'])
@login_required
def admin_orders():
    if current_user.role != 'admin':
        flash("You are not authorized to view this page.", "danger")
        return redirect(url_for('routes.marketplace'))

    # Fetch all approved orders
    orders = Order.query.filter_by(status="Approved").all()

    return render_template('admin_dashboard.html', orders=orders)

@admin_bp.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(admin_bp.static_folder, filename)

# Admin view for managing subscriptions
@admin_bp.route('/subscriptions', methods=['GET', 'POST'])
@login_required
def manage_subscriptions():
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for('routes.index'))

    subscriptions = Subscription.query.all()
    if request.method == 'POST':
        # Add a new subscription offer
        name = request.form.get('name')
        description = request.form.get('description')
        price = float(request.form.get('price'))
        subscription = Subscription(name=name, description=description, price=price)
        db.session.add(subscription)
        db.session.commit()
        flash("Subscription added successfully.", "success")
        return redirect(url_for('admin_routes.manage_subscriptions'))

    return render_template('admin/admin_subscriptions.html', subscriptions=subscriptions)

# Toggle subscription status
@admin_bp.route('/subscriptions/<int:subscription_id>/toggle', methods=['POST'])
@login_required
def toggle_subscription(subscription_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for('index'))

    subscription = Subscription.query.get_or_404(subscription_id)
    subscription.status = 'active' if subscription.status == 'inactive' else 'inactive'
    db.session.commit()
    flash("Subscription status updated.", "success")
    return redirect(url_for('admin_routes.manage_subscriptions'))


#-----------------VIEW ALL TRANSACTIONS-----------------
@admin_bp.route('/transactions')
@login_required
def view_all_transactions():
    """Admin view for all wallet transactions."""
    user_filter = request.args.get('user', type=str)
    kind_filter = request.args.get('kind', type=str)
    status_filter = request.args.get('status', type=str)

    query = WalletTransaction.query.join(Wallet).join(User)

    if user_filter:
        query = query.filter(User.username.ilike(f"%{user_filter}%"))
    if kind_filter:
        query = query.filter(WalletTransaction.kind == kind_filter)
    if status_filter:
        query = query.filter(WalletTransaction.status == status_filter)

    transactions = query.order_by(WalletTransaction.timestamp.desc()).all()

    return render_template('admin/view_transactions.html', transactions=transactions)


# View pending transactions for approval/rejection
@admin_bp.route('/transactions/pending')
@login_required
def view_pending_transactions():
    if not current_user.is_admin:
        abort(403)
    pending = WalletTransaction.query.filter_by(status='pending').all()
    return render_template('admin/pending_transactions.html', transactions=pending)


# Approve a pending transaction
@admin_bp.route('/transactions/approve/<int:txn_id>', methods=['POST'])
@login_required
def approve_transaction(txn_id):
    if not current_user.is_admin:
        abort(403)
    txn = WalletTransaction.query.get_or_404(txn_id)
    if txn.status != 'pending':
        flash('Transaction already processed', 'info')
        return redirect(url_for('admin_routes.view_pending_transactions'))

    settle_transaction(txn.id)
    flash(f'Transaction {txn.id} approved successfully.', 'success')
    return redirect(url_for('admin_routes.view_pending_transactions'))

# Reject a pending transaction
@admin_bp.route('/transactions/reject/<int:txn_id>', methods=['POST'])
@login_required
def reject_transaction(txn_id):
    if not current_user.is_admin:
        abort(403)
    txn = WalletTransaction.query.get_or_404(txn_id)
    if txn.status != 'pending':
        flash('Transaction already processed', 'info')
        return redirect(url_for('admin_routes.view_pending_transactions'))

    txn.status = 'rejected'
    db.session.commit()
    flash(f'Transaction {txn.id} rejected.', 'warning')
    return redirect(url_for('admin_routes.view_pending_transactions'))

# admin_routes.py

@admin_bp.route('/admin/transactions/summary')
@login_required
def transactions_summary():
    total_deposits = db.session.query(db.func.sum(WalletTransaction.amount)).filter_by(transaction_type='Deposit').scalar() or 0
    total_withdrawals = db.session.query(db.func.sum(WalletTransaction.amount)).filter_by(transaction_type='Withdrawal').scalar() or 0
    total_balance = total_deposits - total_withdrawals
    pending = WalletTransaction.query.filter_by(status='Pending').count()
    completed = WalletTransaction.query.filter_by(status='Completed').count()

    return {
        "total_deposits": round(total_deposits, 2),
        "total_withdrawals": round(total_withdrawals, 2),
        "total_balance": round(total_balance, 2),
        "pending": pending,
        "completed": completed
    }

@admin_bp.route('/wallet', methods=['GET'])
@login_required
def admin_wallet():
    """Admin view: shows admin momo numbers, wallet balances and paginated transactions."""
    # simple admin check — adapt as needed
    if not getattr(current_user, 'is_admin', False):
        flash("Access denied: admin only.", "danger")
        return redirect(url_for('routes.index'))

    # admin momo numbers from system configuration (stored key/value)
    admin_momo = SystemConfig.get_value('admin_momo_number', '0248275667')
    admin_telecel = SystemConfig.get_value('admin_telecel_number', '0505446261')

    # wallet listing and totals grouped by currency
    wallets = Wallet.query.order_by(Wallet.currency.desc(), Wallet.balance.desc()).all()
    totals = db.session.query(
        Wallet.currency,
        func.coalesce(func.sum(Wallet.balance), 0).label('total_balance'),
        func.count(Wallet.id).label('wallet_count')
    ).group_by(Wallet.currency).all()

    # transactions query with optional server-side filters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)

    tx_q = WalletTransaction.query.join(Wallet).join(User)

    user_filter = request.args.get('user', type=str)
    kind_filter = request.args.get('kind', type=str)
    status_filter = request.args.get('status', type=str)

    if user_filter:
        tx_q = tx_q.filter(User.username.ilike(f"%{user_filter}%"))
    if kind_filter:
        tx_q = tx_q.filter(WalletTransaction.kind == kind_filter)
    if status_filter:
        tx_q = tx_q.filter(WalletTransaction.status == status_filter)

    tx_q = tx_q.order_by(WalletTransaction.timestamp.desc())

    pagination = tx_q.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        'admin/admin_wallet.html',
        admin_momo=admin_momo,
        admin_telecel=admin_telecel,
        wallets=wallets,
        totals=totals,
        transactions_pag=pagination,
        user_filter=user_filter,
        kind_filter=kind_filter,
        status_filter=status_filter
    )


@admin_bp.route('/wallet/update_numbers', methods=['POST'])
@login_required
def admin_update_wallet_numbers():
    """Update system admin mobile numbers used for MoMo / Telecel cash receipts."""
    if not getattr(current_user, 'is_admin', False):
        flash("Access denied.", "danger")
        return redirect(url_for('routes.index'))

    momo = request.form.get('admin_momo', '').strip()
    telecel = request.form.get('admin_telecel', '').strip()

    # Basic validation (you can expand with regex or country-specific checks)
    if momo and len(momo.replace(" ", "")) < 6:
        flash("Invalid MoMo number.", "danger")
        return redirect(url_for('admin_routes.admin_wallet'))

    if telecel and len(telecel.replace(" ", "")) < 6:
        flash("Invalid Telecel number.", "danger")
        return redirect(url_for('admin_routes.admin_wallet'))

    # persist to SystemConfig
    try:
        SystemConfig.set_value('admin_momo_number', momo)
        SystemConfig.set_value('admin_telecel_number', telecel)
        flash("Admin mobile numbers updated.", "success")
    except Exception as e:
        current_app.logger.exception("Failed to update admin numbers: %s", e)
        flash("Failed to update admin numbers.", "danger")

    return redirect(url_for('admin_routes.admin_wallet'))