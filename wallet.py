from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from flask import Blueprint, current_app, current_app, flash, redirect, render_template, url_for, request, send_file, abort, get_flashed_messages
from flask_login import current_user, login_required
import qrcode, json, io
from sqlalchemy.exc import SQLAlchemyError
from database import db
from models import ExchangeRate, SystemConfig, User, Wallet, WalletTransaction

user_bp = Blueprint('user_routes', __name__)

# ----------------- Helper functions -----------------
def get_or_create_wallet(user_id, *, currency=None, name=None):
    """
    Return the user's wallet. If it does not exist, create one.
    If currency is None we try to derive it from the user's country.
    """
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if wallet:
        return wallet

    # Try to derive currency from the user's country
    user = User.query.get(user_id)
    if not currency:
        currency = country_to_currency(getattr(user, 'country', None), fallback='USD')

    wallet = Wallet(
        user_id=user_id,
        balance=Decimal('0.00'),
        currency=currency,
        name=name or "Main Wallet"
    )
    db.session.add(wallet)
    db.session.flush()  # ensure wallet.id exists for subsequent txns
    db.session.commit()
    return wallet



# Minimal mapping for African countries to currency codes.
# Extend this list if you need more precise local currency choices.
COUNTRY_TO_CURRENCY = {
    "Algeria": "DZD",
    "Angola": "AOA",
    "Benin": "XOF",
    "Botswana": "BWP",
    "Burkina Faso": "XOF",
    "Burundi": "BIF",
    "Cabo Verde": "CVE",
    "Cameroon": "XAF",
    "Central African Republic": "XAF",
    "Chad": "XAF",
    "Comoros": "KMF",
    "Congo": "XAF",
    "Democratic Republic of the Congo": "CDF",
    "Djibouti": "DJF",
    "Egypt": "EGP",
    "Equatorial Guinea": "XAF",
    "Eritrea": "ERN",
    "Eswatini": "SZL",
    "Ethiopia": "ETB",
    "Gabon": "XAF",
    "Gambia": "GMD",
    "Ghana": "GHS",
    "Guinea": "GNF",
    "Guinea-Bissau": "XOF",
    "Ivory Coast": "XOF",
    "Kenya": "KES",
    "Lesotho": "LSL",
    "Liberia": "LRD",
    "Libya": "LYD",
    "Madagascar": "MGA",
    "Malawi": "MWK",
    "Mali": "XOF",
    "Mauritania": "MRU",
    "Mauritius": "MUR",
    "Morocco": "MAD",
    "Mozambique": "MZN",
    "Namibia": "NAD",
    "Niger": "XOF",
    "Nigeria": "NGN",
    "Rwanda": "RWF",
    "Senegal": "XOF",
    "Seychelles": "SCR",
    "Sierra Leone": "SLL",
    "South Africa": "ZAR",
    "Sudan": "SDG",
    "Togo": "XOF",
    "Tunisia": "TND",
    "Uganda": "UGX",
    "Zambia": "ZMW",
    "Zimbabwe": "ZWL",
}

def country_to_currency(country_name: str, fallback: str = "USD") -> str:
    """
    Return an ISO currency code for a given country name.
    Fallback defaults to 'USD' if unknown (you can change).
    """
    if not country_name:
        return fallback
    code = COUNTRY_TO_CURRENCY.get(country_name.strip(), None)
    return code or fallback


def credit_wallet(wallet, amount, source, reference_type=None, reference_id=None, description=None, status='completed'):
    """
    Create a credit wallet transaction. If status == 'completed', update wallet.balance immediately.
    Returns the WalletTransaction object.
    """
    amount = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    txn = WalletTransaction(
        wallet_id=wallet.id,
        amount=amount,
        kind='credit',
        source=source,
        status=status,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        timestamp=datetime.utcnow()
    )
    db.session.add(txn)
    if status == 'completed':
        wallet.balance = (Decimal(wallet.balance) + amount).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    db.session.flush()
    current_app.logger.info("Created wallet credit txn %s for wallet %s amount=%s status=%s", txn.id, wallet.id, txn.amount, txn.status)
    return txn


def debit_wallet(wallet, amount, source, reference_type=None, reference_id=None, description=None, status='completed'):
    """
    Create a debit wallet transaction. Amount stored as positive in `amount` column but 'kind' = 'debit'.
    If status == 'completed', subtract from wallet.balance.
    """
    amount = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    txn = WalletTransaction(
        wallet_id=wallet.id,
        amount=-amount,  # store negative so listing shows debit as negative
        kind='debit',
        source=source,
        status=status,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        timestamp=datetime.utcnow()
    )
    db.session.add(txn)
    if status == 'completed':
        wallet.balance = (Decimal(wallet.balance) - amount).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    db.session.flush()
    current_app.logger.info("Created wallet debit txn %s for wallet %s amount=%s status=%s", txn.id, wallet.id, txn.amount, txn.status)
    return txn

def settle_transaction(txn_id):
    """Mark pending txn completed and apply to wallet balance atomically."""
    txn = WalletTransaction.query.get(txn_id)
    if not txn:
        current_app.logger.warning("settle_transaction: txn %s not found", txn_id)
        return False
    if txn.status != 'pending':
        current_app.logger.info("settle_transaction: txn %s status is %s (not pending)", txn_id, txn.status)
        return False
    wallet = Wallet.query.get(txn.wallet_id)
    if not wallet:
        current_app.logger.error("settle_transaction: wallet %s not found for txn %s", txn.wallet_id, txn_id)
        return False

    try:
        # txn.amount may be negative for debits; add to balance works for both credit/debit
        wallet.balance = (Decimal(wallet.balance) + Decimal(txn.amount)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        txn.status = 'completed'
        db.session.commit()
        current_app.logger.info("settle_transaction: txn %s settled, new wallet %s balance=%s", txn.id, wallet.id, wallet.balance)
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("settle_transaction: failed to settle txn %s: %s", txn_id, e)
        return False

# ----------------- Routes -----------------
@user_bp.route('/wallet')
@login_required
def wallet_page():
    """
    Wallet page with paginated transactions.
    Query param: ?page=1
    """
    # pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 8

    # get or create user wallet
    wallet = get_or_create_wallet(current_user.id)

    # fetch admin momo numbers dynamically from database
    admin_momo = SystemConfig.get_value('admin_momo_number', '0248275667')
    admin_telecel = SystemConfig.get_value('admin_telecel_number', '0505446261')

    # only show momo and telecel cash numbers
    admin_numbers = [
        {"network": "MTN Mobile Money", "number": admin_momo},
        {"network": "Telecel Cash", "number": admin_telecel},
    ]

    # get wallet transactions (newest first)
    transactions_q = (
        WalletTransaction.query
        .filter_by(wallet_id=wallet.id)
        .order_by(WalletTransaction.timestamp.desc())
    )

    # paginate transactions
    pagination = transactions_q.paginate(page=page, per_page=per_page, error_out=False)

    # render template
    return render_template('wallet.html', wallet=wallet, transactions_pag=pagination, admin_numbers=admin_numbers)


@user_bp.route('/wallet/topup', methods=['POST'])
@login_required
def wallet_topup():
    amount_raw = request.form.get('amount')
    selected_network = request.form.get('network')
    user_number = request.form.get('user_number')  # New field

    if not selected_network:
        flash('Please select the network you sent money to.', 'danger')
        return redirect(url_for('user_routes.wallet_page'))

    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise ValueError("Invalid amount")
    except Exception:
        flash('Invalid top-up amount.', 'danger')
        return redirect(url_for('user_routes.wallet_page'))

    wallet = get_or_create_wallet(current_user.id)

    txn = credit_wallet(
        wallet,
        amount,
        source='topup',
        reference_type='wallet',
        reference_id=wallet.id,
        description=f"Top-up via {selected_network} (Pending admin confirmation)",
        status='pending'
    )

    # Log user number for admin visibility
    txn.metadata = {'user_number': user_number, 'network': selected_network}

    db.session.commit()

    flash(f'Top-up request submitted via {selected_network}. Awaiting admin confirmation.', 'info')
    return redirect(url_for('user_routes.wallet_page'))


@user_bp.route('/wallet/withdraw', methods=['POST'])
@login_required
def wallet_withdraw():
    amount_raw = request.form.get('amount')
    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise ValueError("Invalid amount")
    except Exception:
        flash('Invalid withdrawal amount', 'danger')
        return redirect(url_for('user_routes.wallet_page'))

    wallet = get_or_create_wallet(current_user.id)
    if Decimal(wallet.balance) < amount:
        flash('Insufficient balance', 'danger')
        return redirect(url_for('user_routes.wallet_page'))

    txn = debit_wallet(wallet, amount, source='withdrawal', reference_type='wallet', reference_id=wallet.id, description='User withdrawal request', status='pending')
    db.session.commit()
    flash('Withdrawal requested. Processing by admin.', 'success')
    return redirect(url_for('user_routes.wallet_page'))


# ----------------- Payment simulation -----------------
@user_bp.route('/wallet/start_payment/<int:txn_id>')
@login_required
def start_payment(txn_id):
    """
    Simulate a payment flow for either wallet top-up or subscription purchase.
    Completes the pending transaction and triggers follow-up actions.
    """
    txn = WalletTransaction.query.get(txn_id)
    if not txn or txn.wallet.user_id != current_user.id:
        flash('Transaction not found.', 'danger')
        return redirect(url_for('user_routes.wallet_page'))

    wallet = txn.wallet

    # Check sufficient funds for debits
    if txn.kind == 'debit' and Decimal(wallet.balance) + Decimal(txn.amount) < 0:
        flash("Insufficient balance to complete payment.", "danger")
        txn.status = "failed"
        db.session.commit()
        return redirect(url_for('user_routes.wallet_page'))

    # Simulate successful payment
    txn.status = "completed"
    wallet.balance = (Decimal(wallet.balance) + Decimal(txn.amount)).quantize(Decimal("0.01"))
    db.session.commit()

    # Handle subscription activation after payment
    if txn.source == "subscription" and txn.reference_type == "subscription":
        from models import Seller, SellerSubscription, Subscription
        seller = Seller.query.filter_by(user_id=current_user.id).first()
        subscription = Subscription.query.get(txn.reference_id)

        if seller and subscription:
            valid_until = datetime.utcnow() + timedelta(days=subscription.validity_period)
            new_subscription = SellerSubscription(
                seller_id=seller.id,
                subscription_id=subscription.id,
                subscribed_on=datetime.utcnow(),
                valid_until=valid_until
            )
            db.session.add(new_subscription)
            db.session.commit()
            flash(f"Payment successful! Subscribed to {subscription.name} until {valid_until.date()}.", "success")
            return redirect(url_for('seller_routes.available_seller_subscriptions'))

    # Handle normal top-ups
    if txn.source == "topup":
        flash(f"Wallet top-up of {abs(txn.amount)} successful!", "success")
        return redirect(url_for('user_routes.wallet_page'))

    flash("Payment completed successfully.", "success")
    return redirect(url_for('user_routes.wallet_page'))

# ----------------- Transaction processing -----------------
def get_exchange_rate(from_currency, to_currency):
    """Fetch conversion rate; default 1.0 if same currency."""
    if from_currency == to_currency:
        return Decimal('1.0')
    rate = ExchangeRate.query.filter_by(from_currency=from_currency, to_currency=to_currency).first()
    return Decimal(rate.rate) if rate else None

# Process a wallet transaction with mixed rules
def process_transaction(sender_wallet, receiver_wallet, amount):
    """Mixed rule: auto or pending depending on amount/currency."""
    amount = Decimal(amount)
    threshold = Decimal(SystemConfig.get_value("transaction_threshold", "5000.00"))

    same_currency = sender_wallet.currency == receiver_wallet.currency
    requires_approval = False

    # Rule 1: Cross-border always needs exchange rate
    if not same_currency:
        rate = get_exchange_rate(sender_wallet.currency, receiver_wallet.currency)
        if not rate:
            raise ValueError("No exchange rate found between selected countries.")
        converted_amount = (amount * rate).quantize(Decimal("0.01"))
        requires_approval = True  # cross-country → admin approval
    else:
        converted_amount = amount

    # Rule 2: Local big transaction
    if same_currency and amount > threshold:
        requires_approval = True

    status = "pending" if requires_approval else "completed"

    # Debit sender
    debit_wallet(sender_wallet, amount, source="transfer", reference_type="user", reference_id=receiver_wallet.user_id,
                 description=f"Transfer to {receiver_wallet.user.username}", status=status)

    # Credit receiver
    credit_wallet(receiver_wallet, converted_amount, source="transfer", reference_type="user",
                  reference_id=sender_wallet.user_id,
                  description=f"Transfer from {sender_wallet.user.username}", status=status)

    db.session.commit()
    return status

@user_bp.route('/wallet/qr')
@login_required
def wallet_qr():
    """Generate a QR code for this user's wallet."""
    wallet = get_or_create_wallet(current_user.id)
    data = {
        'qr_code_id': wallet.qr_code_id,
        'username': current_user.username,
        'currency': wallet.currency
    }
    qr = qrcode.make(json.dumps(data))
    img_io = io.BytesIO()
    qr.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

