# affiliate_routes.py
from flask import Blueprint, current_app, request, jsonify, redirect, url_for, render_template, session, flash, abort
from flask_login import login_required, current_user
from database import db
from models import Affiliate, Referral, Product, Order, Seller, CommissionPlan, User, Buyer, CommissionSettings, CommissionPlan
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import requests, logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

affiliate_bp = Blueprint('affiliate_routes', __name__, template_folder='templates/affiliate')


def get_affiliate_or_abort():
    """Return the Affiliate model associated with current_user or abort 403."""
    if not current_user or not current_user.is_authenticated:
        abort(401)  # Unauthorized
    affiliate = getattr(current_user, 'affiliate_account', None)
    if not affiliate:
        # Not an affiliate yet
        abort(403)
    return affiliate


# ---- Affiliate signup endpoint (API) ----
@affiliate_bp.route('/register', methods=['POST'])
@login_required
def register_affiliate():
    if getattr(current_user, 'affiliate_account', None):
        return jsonify({'error': 'You are already an affiliate.'}), 400

    # create a simple referral code - you can replace with a more robust generator
    referral_code = f"REF-{current_user.id}"
    affiliate = Affiliate(user_id=current_user.id, referral_code=referral_code)
    db.session.add(affiliate)
    db.session.commit()
    return jsonify({'message': 'Affiliate account created!', 'referral_code': referral_code}), 201


# ---- Marketing Tools page ----
@affiliate_bp.route('/tools', methods=['GET'])
@login_required
def tools():
    affiliate = get_affiliate_or_abort()
    recent_products = Product.query.filter(func.lower(Product.status) == 'approved').order_by(Product.date_added.desc()).limit(12).all()
    # show a small list of recent referral records for this affiliate (optional)
    recent_referrals = Referral.query.filter_by(affiliate_id=affiliate.id).order_by(Referral.timestamp.desc()).limit(8).all()

    return render_template(
        'affiliate/tools.html',
        affiliate_code=affiliate.referral_code,
        products=recent_products,
        recent_referrals=recent_referrals
    )

def get_affiliate_or_abort():
    if not current_user or not current_user.is_authenticated:
        abort(401)
    affiliate = getattr(current_user, 'affiliate_account', None)
    if not affiliate:
        abort(403)
    return affiliate

# --- Diagnostic route to list some registered rules (helpful during debugging) ---
@affiliate_bp.route('/_routes', methods=['GET'])
@login_required
def _list_routes():
    """
    Temporary debug endpoint — lists app url_map rules.
    Remove this in production.
    """
    rules = []
    for rule in current_app.url_map.iter_rules():
        rules.append({
            "rule": str(rule),
            "endpoint": rule.endpoint,
            "methods": sorted([m for m in rule.methods if m not in ('HEAD', 'OPTIONS')])
        })
    return jsonify({"routes": rules})

# ---- Generate referral link (primary POST) ----
# accept both /generate_link and /generate-link (alias), both POST only
@affiliate_bp.route('/generate_link', methods=['POST'])
@affiliate_bp.route('/generate-link', methods=['POST'])
@login_required
def generate_referral_link():
    """
    POST JSON: { product_id: <int> }
    Returns JSON: { referral_link, referral_id, created: true|false } or error.
    """
    affiliate = get_affiliate_or_abort()

    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'error': 'Missing product_id'}), 400

    product = Product.query.get(product_id)
    # case-insensitive check for status to avoid mismatches between 'Approved' vs 'approved'
    if not product or (getattr(product, 'status', '') or '').lower() != 'approved':
        return jsonify({'error': 'Product not found or not available'}), 404

    # Build external product URL and referral link
    product_url = url_for('seller_routes.product_details', product_id=product.id, _external=True)
    referral_link = f"{product_url}?ref={affiliate.referral_code}"

    try:
        existing = Referral.query.filter_by(
            affiliate_id=affiliate.id,
            product_id=product.id
        ).filter(Referral.status.in_(['generated', 'pending', 'approved'])).first()

        if existing:
            logger.debug("Existing referral found: affiliate=%s product=%s referral_id=%s", affiliate.id, product.id, existing.id)
            return jsonify({
                'referral_link': referral_link,
                'referral_id': existing.id,
                'created': False
            }), 200

        new_referral = Referral(
            affiliate_id=affiliate.id,
            product_id=product.id,
            commission=0.0,
            status='generated',
            timestamp=datetime.utcnow()
        )
        db.session.add(new_referral)
        db.session.commit()

        logger.info("Created referral: affiliate=%s product=%s referral_id=%s", affiliate.id, product.id, new_referral.id)
        return jsonify({
            'referral_link': referral_link,
            'referral_id': new_referral.id,
            'created': True
        }), 201

    except Exception as e:
        current_app.logger.exception("Error generating referral link")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500


# ---- Fetch-only endpoint: get existing referral link for a product ----
@affiliate_bp.route('/get_referral_link/<int:product_id>', methods=['GET'])
@login_required
def get_referral_link(product_id):
    affiliate = get_affiliate_or_abort()

    product = Product.query.get(product_id)
    if not product or (getattr(product, 'status', '') or '').lower() != 'approved':
        return jsonify({'error': 'Product not found or not available'}), 404

    existing_referral = Referral.query.filter_by(
        affiliate_id=affiliate.id,
        product_id=product.id
    ).filter(Referral.status.in_(['generated', 'pending', 'approved'])).first()

    if not existing_referral:
        return jsonify({'referral_link': None, 'message': 'No referral link found. Please generate one first.'}), 404

    product_url = url_for('seller_routes.product_details', product_id=product.id, _external=True)
    referral_link = f"{product_url}?ref={affiliate.referral_code}"

    return jsonify({
        'referral_link': referral_link,
        'referral_id': existing_referral.id,
        'created': False
    }), 200

# ---- Unified Affiliate Dashboard ----
@affiliate_bp.route('/dashboard', methods=['GET'])
@login_required
def affiliate_dashboard():
    affiliate = get_affiliate_or_abort()

    # pagination for products
    page = request.args.get('page', 1, type=int)
    products_query = Product.query.filter_by(status='approved').order_by(Product.date_added.desc())
    products = products_query.paginate(page=page, per_page=12, error_out=False)

    # referrals for this affiliate
    referrals_q = Referral.query.filter_by(affiliate_id=affiliate.id).order_by(Referral.timestamp.desc())
    referrals = referrals_q.all()

    # basic stats
    total_earnings = sum(r.commission or 0.0 for r in referrals if getattr(r, 'status', None) == 'approved')
    now = datetime.utcnow()
    new_referrals = sum(1 for r in referrals if r.timestamp and r.timestamp.month == now.month and r.timestamp.year == now.year)
    pending_commissions = sum(r.commission or 0.0 for r in referrals if getattr(r, 'status', None) == 'pending')
    active_campaigns = Product.query.filter_by(status='approved').count()

    return render_template(
        'affiliate/dashboard.html',
        total_earnings=total_earnings,
        new_referrals=new_referrals,
        pending_commissions=pending_commissions,
        active_campaigns=active_campaigns,
        referrals=[{
            'product_id': r.product_id,
            'commission': r.commission,
            'timestamp': r.timestamp
        } for r in referrals],
        affiliate_code=affiliate.referral_code,
        products=products,           
        approved_products=products.items,
        pagination=products
    )


# ---- Recent Activity page ----
@affiliate_bp.route('/activity', methods=['GET'])
@login_required
def activity():
    affiliate = get_affiliate_or_abort()
    referrals = Referral.query.filter_by(affiliate_id=affiliate.id).order_by(Referral.timestamp.desc()).limit(200).all()

    return render_template(
        'affiliate/activity.html',
        referrals=[{
            'product_id': r.product_id,
            'commission': r.commission,
            'timestamp': r.timestamp
        } for r in referrals]
    )


@affiliate_bp.route('/approved_products')
@login_required
def approved_products():
    if current_user.role != 'affiliate':
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('routes.marketplace'))

    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search_query', '')

    # Only show approved products
    query = Product.query.filter_by(status='Approved')

    # Optional: allow searching
    if search_query:
        query = query.filter(Product.name.ilike(f"%{search_query}%"))

    products = query.order_by(Product.date_added.desc()).paginate(page=page, per_page=10)

    return render_template(
        'affiliate/approved_products.html',
        products=products.items,
        pagination=products,
        search_query=search_query
    )


# ---- Search page (serves template, actual results via affiliate_search endpoint) ----
@affiliate_bp.route('/search', methods=['GET'])
@login_required
def search_page():
    affiliate = get_affiliate_or_abort()
    return render_template('affiliate/search.html')


# --- Calculator page route ---
@affiliate_bp.route('/calculator', methods=['GET'])
@login_required
def calculator():
    """
    Render the Affiliate calculator page.
    The JS on the page fetches live rates from exchangerate.host (client-side).
    """
    supported_currencies = [
        'USD', 'EUR', 'GBP', 'NGN', 'KES', 'ZAR', 'CAD', 'AUD', 'CNY', 'JPY', 'XOF'
    ]
    context = {
        'default_currency': 'USD',
        'supported_currencies': supported_currencies,
    }
    return render_template('affiliate/calculator.html', **context)

@affiliate_bp.route('/convert_rate', methods=['GET'])
@login_required
def convert_rate():
    """
    Server-side endpoint to fetch currency conversion from exchangerate.host.
    Prevents CORS/network issues for client-side calls.
    """
    base = request.args.get('from', 'GHS').upper()
    target = request.args.get('to', 'USD').upper()
    amount = request.args.get('amount', 0, type=float)

    try:
        url = f"https://api.exchangerate.host/convert?from={base}&to={target}&amount={amount}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return jsonify({
            "success": True,
            "rate": data.get("info", {}).get("rate"),
            "converted": data.get("result"),
            "date": data.get("date"),
        })
    except Exception as e:
        print("Error fetching rates:", e)
        return jsonify({"success": False, "error": str(e)}), 500
    
# ---- ANALYSIS PAGE ----
@affiliate_bp.route('/analysis', methods=['GET'])
@login_required
def analysis():
    """
    Affiliate Performance Analysis Page
    -----------------------------------
    Displays insights like conversion rates, top-performing products,
    and basic referral analytics. Data is fetched via AJAX endpoints.
    """
    affiliate = get_affiliate_or_abort()

    # Fetch top approved products by views
    top_products = (
        Product.query
        .filter_by(status='approved')
        .order_by(Product.view_count.desc())
        .limit(10)
        .all()
    )

    return render_template(
        'affiliate/analysis.html',
        active_tab='analysis',
        approved_products=top_products
    )


@affiliate_bp.route('/referrals', methods=['GET'])
@login_required
def referrals():
    """
    Affiliate Referrals Page
    ------------------------
    Displays real referral data for the logged-in affiliate
    with pagination and sorting.
    """
    affiliate = get_affiliate_or_abort()

    # --- Sorting and Pagination ---
    sort_option = request.args.get('sort', 'date_desc')  # default newest first
    page = request.args.get('page', 1, type=int)
    per_page = 10

    # Base query
    query = (
        db.session.query(
            Referral,
            Product.name.label('product_name'),
            Product.price.label('product_price'),
            User.username.label('buyer_name'),
            User.email.label('buyer_email')
        )
        .join(Product, Referral.product_id == Product.id)
        .outerjoin(Order, Referral.order_id == Order.id)
        .outerjoin(Buyer, Buyer.id == Order.buyer_id)
        .outerjoin(User, User.id == Buyer.user_id)
        .filter(Referral.affiliate_id == affiliate.id)
    )

    # Sorting logic
    if sort_option == 'commission_asc':
        query = query.order_by(Referral.commission.asc())
    elif sort_option == 'commission_desc':
        query = query.order_by(Referral.commission.desc())
    elif sort_option == 'date_asc':
        query = query.order_by(Referral.timestamp.asc())
    else:  # date_desc
        query = query.order_by(Referral.timestamp.desc())

    # Pagination
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # Format results
    referral_list = []
    for ref, product_name, product_price, buyer_name, buyer_email in pagination.items:
        referral_list.append({
            "product": product_name or "Unknown Product",
            "product_price": product_price or 0.00,
            "buyer": buyer_name or "Guest Buyer",
            "email": buyer_email or "N/A",
            "commission": f"{ref.commission:.2f}",
            "status": ref.status.capitalize(),
            "date": ref.timestamp.strftime("%Y-%m-%d"),
        })

    return render_template(
        'affiliate/referrals.html',
        active_tab='referrals',
        referrals=referral_list,
        pagination=pagination,
        sort_option=sort_option
    )

# ---- Helper to get active commission percent for a product ----
def get_active_commission_percent_for_product(product: Product, db_session=db.session):
    """
    Returns the commission percent (e.g. 10.0 for 10%) to use for affiliate payouts for this product.
    Resolution order:
      1. CommissionSettings.global_affiliate_percent if admin_control_enabled is True and value not None
      2. product.commission_plan.commission_rate if attached
      3. fallback to 0.0
    """
    settings = db_session.query(CommissionSettings).order_by(CommissionSettings.id.desc()).first()
    if settings and settings.admin_control_enabled and settings.global_affiliate_percent is not None:
        return float(settings.global_affiliate_percent)
    # fallback to product-specific commission plan
    if product and getattr(product, 'commission_plan', None):
        try:
            return float(product.commission_plan.commission_rate or 0.0)
        except Exception:
            return 0.0
    return 0.0

# ---- ANALYTICS FETCH (AJAX) ----
@affiliate_bp.route('/affiliate/fetch_analytics', methods=['GET'])
@login_required
def fetch_analytics():
    """
    Provides live JSON analytics data for charts and summaries.
    Called by AJAX from the analysis dashboard.
    """
    affiliate = get_affiliate_or_abort()
    referrals = Referral.query.filter_by(affiliate_id=affiliate.id).all()

    total_earnings = sum(r.commission or 0 for r in referrals if getattr(r, 'status', None) == 'approved')
    pending_commissions = sum(r.commission or 0 for r in referrals if getattr(r, 'status', None) == 'pending')
    new_referrals = sum(1 for r in referrals if r.timestamp and r.timestamp.month == datetime.utcnow().month)

    return jsonify({
        "total_earnings": total_earnings,
        "pending_commissions": pending_commissions,
        "new_referrals": new_referrals,
        "active_campaigns": Product.query.filter_by(status='approved').count()
    })


# ---- PREDICTION FETCH (AJAX) ----
@affiliate_bp.route('/affiliate/fetch_prediction', methods=['GET'])
@login_required
def fetch_prediction():
    """
    Predicts potential next-month commissions (demo only).
    Replace with a real ML or stats-based projection.
    """
    affiliate = get_affiliate_or_abort()
    referrals = (
        Referral.query.filter_by(affiliate_id=affiliate.id)
        .order_by(Referral.timestamp.desc())
        .limit(50)
        .all()
    )

    commissions = [r.commission or 0 for r in referrals]
    avg_commission = (sum(commissions) / len(commissions)) if commissions else 0
    predicted_next_month = avg_commission * 1.10  # Simple +10% growth assumption

    return jsonify({
        "average_commission": avg_commission,
        "predicted_next_month": predicted_next_month
    })


# ---- Search API used by client-side JS ----
@affiliate_bp.route('/affiliate_search')
@login_required
def affiliate_search():
    query = request.args.get('query', '').strip()
    category = request.args.get('category', 'products')

    results = []
    if not query:
        return jsonify(results)

    if category == "products":
        prods = Product.query.filter(Product.name.ilike(f"%{query}%") | Product.description.ilike(f"%{query}%")).limit(60).all()
        results = [{
            'id': p.id,
            'name': p.name,
            'description': p.description or '',
            'price': p.price,
            'category': p.category,
            'image_url': (p.images[0].image_url.replace('\\', '/') if p.images else url_for('static', filename='default_image.jpg'))
        } for p in prods]

    elif category == "sellers":
        sellers = Seller.query.filter(Seller.username.ilike(f"%{query}%")).limit(60).all()
        results = [{'id': s.id, 'username': s.username} for s in sellers]

    elif category == "commissions":
        plans = CommissionPlan.query.filter(CommissionPlan.plan_name.ilike(f"%{query}%")).limit(60).all()
        results = [{'id': p.id, 'plan_name': p.plan_name, 'commission_rate': p.commission_rate} for p in plans]

    return jsonify(results)


# ---- Utility to create referral after order completes (call from order processing) ----
def create_referral(affiliate, product, order, commission):
    referral = Referral(
        affiliate_id=affiliate.id,
        product_id=product.id,
        order_id=order.id,
        commission=commission,
        status='pending',
        timestamp=datetime.utcnow()
    )
    db.session.add(referral)
    db.session.commit()
    return referral


#-----------------CHAT FUNCTIONALITY--------------------
@affiliate_bp.route('/chat')
@login_required
def affiliate_chat():
    return render_template('chat_page.html', user=current_user)
