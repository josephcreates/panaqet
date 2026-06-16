from flask import Blueprint, request, jsonify, redirect, url_for, render_template, session
from flask_login import login_required, current_user
from database import db
import sqlite3  # Or use your preferred database connection module
from models import Affiliate, Referral, Product, Order, Seller, CommissionPlan
from datetime import datetime

affiliate_bp = Blueprint('affiliate_routes', __name__)

@affiliate_bp.route('/register', methods=['POST'])
@login_required
def register_affiliate():
    if current_user.affiliate_account:
        return jsonify({'error': 'You are already an affiliate.'}), 400
    
    referral_code = f"REF-{current_user.id}"
    affiliate = Affiliate(user_id=current_user.id, referral_code=referral_code)
    db.session.add(affiliate)
    db.session.commit()
    return jsonify({'message': 'Affiliate account created!', 'referral_code': referral_code}), 201

@affiliate_bp.route('/generate_link/<int:product_id>', methods=['GET'])
@login_required
def generate_referral_link(product_id):
    affiliate = current_user.affiliate_account
    if not affiliate:
        return jsonify({'error': 'You are not an affiliate.'}), 403

    product = Product.query.get_or_404(product_id)
    referral_link = url_for('seller_routes.product_details', product_id=product.id, _external=True) + f"?ref={affiliate.referral_code}"

    # Save the referral link generation event
    new_referral = Referral(
        affiliate_id=affiliate.id,
        product_id=product.id,
        commission=0.0  # Initial commission set to 0, updated after a sale
    )
    db.session.add(new_referral)
    db.session.commit()

    return jsonify({'referral_link': referral_link})


@affiliate_bp.route('/dashboard', methods=['GET'])
@login_required
def affiliate_dashboard():
    affiliate = current_user.affiliate_account
    if not affiliate:
        return jsonify({'error': 'You are not an affiliate.'}), 403

    category_filter = request.args.get('category_filter', '')
    search_query = request.args.get('query', '')
    page = request.args.get('page', 1, type=int)
    sort_option = request.args.get('sort', 'relevance')  # Added sorting option

    # Fetch distinct product categories
    categories = [c[0] for c in db.session.query(Product.category).distinct().all()]

    # Fetch all approved products with optional category filter
    products_query = Product.query.filter_by(status='approved')

    # Search query logic
    if search_query:
        search_pattern = f'%{search_query}%'
        products_query = products_query.filter(
            (Product.name.ilike(search_pattern)) | (Product.description.ilike(search_pattern))
        )

    # Apply category filter if provided
    if category_filter:
        products_query = products_query.filter(Product.category == category_filter)

    # Sorting options
    if sort_option == 'price_asc':
        products_query = products_query.order_by(Product.price.asc())
    elif sort_option == 'price_desc':
        products_query = products_query.order_by(Product.price.desc())
    elif sort_option == 'newest':
        products_query = products_query.order_by(Product.date_added.desc())
    elif sort_option == 'condition':
        products_query = products_query.order_by(Product.condition.asc())
    elif sort_option == 'brand':
        products_query = products_query.order_by(Product.brand.asc())
    else:
        products_query = products_query.order_by(Product.date_added.desc())  # Default sorting by newest

    # Paginate approved products (Default View)
    products = products_query.paginate(page=page, per_page=12)

    # Affiliate data and stats
    affiliate_code = affiliate.referral_code
    referrals = Referral.query.filter_by(affiliate_id=affiliate.id).all()

    total_earnings = sum(r.commission for r in referrals if r.status == 'approved')
    new_referrals = sum(1 for r in referrals if r.timestamp.month == datetime.utcnow().month)
    pending_commissions = sum(r.commission for r in referrals if r.status == 'pending')
    active_campaigns = Product.query.filter_by(status='approved').count()

    # Handle pagination for the selected products
    pagination = products  # Directly using the products pagination object

    return render_template(
        'affiliate_dashboard.html',
        total_earnings=total_earnings,
        new_referrals=new_referrals,
        pending_commissions=pending_commissions,
        active_campaigns=active_campaigns,
        referrals=[{
            'product_id': r.product_id,
            'commission': r.commission,
            'timestamp': r.timestamp,
            'category': r.product.category
        } for r in referrals],
        categories=categories,
        category_filter=category_filter,
        search_query=search_query,
        products=products.items,  # Pass the products to display
        pagination=pagination,   # Pass pagination for affiliate dashboard
        affiliate_code=affiliate_code,
        sort_option=sort_option  # Pass the sort option
    )


def create_referral(affiliate, product, order, commission):
    referral = Referral(
        affiliate_id=affiliate.id,
        product_id=product.id,
        order_id=order.id,
        commission=commission
    )
    db.session.add(referral)
    db.session.commit()


def get_analytics_data_from_db():
    user_id = session.get('user_id')  # Assuming user_id is stored in the session
    
    if not user_id:
        return None  # Handle cases where the user is not logged in

    # Your DB query follows
    connection = sqlite3.connect('your_database.db')
    cursor = connection.cursor()
    cursor.execute("SELECT total_earnings, new_referrals, pending_commissions, active_campaigns FROM affiliate_data WHERE user_id=?", (user_id,))
    data = cursor.fetchone()
    connection.close()

    if data:
        return {
            'total_earnings': data[0],
            'new_referrals': data[1],
            'pending_commissions': data[2],
            'active_campaigns': data[3]
        }
    else:
        return None
    
from flask_login import current_user

def get_prediction_data_from_db_or_algorithm():
    user_id = current_user.id  # Get the current user's ID from the session

    # Fetch data from database (e.g., past sales or campaign performance)
    connection = sqlite3.connect('your_database.db')
    cursor = connection.cursor()

    # Example query to fetch historical data
    cursor.execute("SELECT campaign_id, sales_performance FROM campaigns WHERE user_id=?", (user_id,))
    
    # Fetch the result
    data = cursor.fetchall()

    # Close the connection
    connection.close()

    if data:
        # Placeholder for a simple prediction algorithm (e.g., linear regression or other methods)
        predictions = []
        for campaign in data:
            campaign_id, performance = campaign
            # Here, apply your prediction logic
            predicted_performance = performance * 1.1  # Example: a 10% increase in performance
            predictions.append({'campaign_id': campaign_id, 'predicted_performance': predicted_performance})

        return predictions
    else:
        return None

@affiliate_bp.route('/affiliate/fetch_analytics', methods=['GET'])
def fetch_analytics():
    # Query the database for affiliate performance data
    analytics_data = get_analytics_data_from_db()
    return jsonify(analytics_data)

@affiliate_bp.route('/affiliate/fetch_prediction', methods=['GET'])
def fetch_prediction():
    # Query the database or use an algorithm for prediction
    prediction_data = get_prediction_data_from_db_or_algorithm()
    return jsonify(prediction_data)


@affiliate_bp.route('/affiliate_search')
def affiliate_search():
    query = request.args.get('query', '').strip()
    category = request.args.get('category', 'products')

    results = []
    
    if not query:
        return jsonify(results)  # Return empty if no query
    
    if category == "products":
        products = Product.query.filter(Product.name.ilike(f"%{query}%")).all()
        results = [f"Product: {p.name} - ₵{p.price}" for p in products]

    elif category == "sellers":
        sellers = Seller.query.filter(Seller.username.ilike(f"%{query}%")).all()
        results = [f"Seller: {s.username}" for s in sellers]

    elif category == "commissions":
        plans = CommissionPlan.query.filter(CommissionPlan.plan_name.ilike(f"%{query}%")).all()
        results = [f"Commission Plan: {p.plan_name} - {p.commission_rate}%" for p in plans]

    return jsonify(results)