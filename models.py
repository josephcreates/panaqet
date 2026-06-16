import sqlite3
from flask_login import UserMixin
from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash
from wtforms import SubmitField
from database import db
from datetime import datetime, timedelta


class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    email = db.Column(db.String(120), index=True, unique=True, nullable=False)
    country_code = db.Column(db.String(5), nullable=True)
    phone_number = db.Column(db.String(15), unique=True, nullable=True)
    country = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(300))
    role = db.Column(db.String(20))
    date_joined = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    profile_image = db.Column(db.String(255), nullable=False, default='default_profile.png')
    cart_items = db.relationship('Cart', back_populates='user')
    user_theme = db.Column(db.String(50), default='light')
    preferred_language = db.Column(db.String(10), default='en')  # Default language is English
    id_type = db.Column(db.String(50), nullable=True)
    id_front_image = db.Column(db.String(255), nullable=True)
    id_back_image = db.Column(db.String(255), nullable=True)
    signup_complete = db.Column(db.Boolean, default=False)
    is_first_login = db.Column(db.Boolean, default=True)
    store_name = db.Column(db.String(100), nullable=True)
    store_description = db.Column(db.String(255), nullable=True)
    store_logo = db.Column(db.String(255), nullable=True)

    # Updated relationship to resolve overlap issues
    affiliate_account = db.relationship('Affiliate', back_populates='user', uselist=False, overlaps="affiliate_account_ref")

    @property
    def is_seller(self):
        return self.role == 'seller'
    
    @property
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def is_affiliate(self):
        return self.role == 'affiliate'
    
    def set_theme(self, theme):
        self.user_theme = theme
        db.session.commit()
    
    def get_theme(self):
        return self.user_theme

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def update_location(self, new_location):
        self.country = new_location  # Update country instead of location
        db.session.commit()

        # If the user is a seller, update the Seller's location
        if self.role == 'seller':
            seller = Seller.query.filter_by(user_id=self.id).first()
            if seller:
                seller.location = new_location
                db.session.commit()
    
    def set_language(self, language):
        self.preferred_language = language
        db.session.commit()

    def get_language(self):
        return self.preferred_language
    
    # Hook to populate the buyer or seller table
    @classmethod
    def create_user(cls, username, email, password, role, country, phone_number=None):
        user = cls(username=username, email=email, role=role, country=country, phone_number=phone_number)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # If the user is a seller, create a corresponding seller entry
        if user.is_seller:
            seller = Seller(user_id=user.id, username=user.username, email=user.email)
            db.session.add(seller)
        
        # If the user is a buyer, create a corresponding buyer entry
        if not user.is_seller:
            buyer = Buyer(user_id=user.id, username=user.username, email=user.email)
            db.session.add(buyer)

        db.session.commit()

        return user
    

product_commission = db.Table(
    'product_commission',
    db.Column('product_id', db.Integer, db.ForeignKey('product.id'), primary_key=True),
    db.Column('commission_plan_id', db.Integer, db.ForeignKey('commission_plans.id'), primary_key=True)
)


class Admin(db.Model, UserMixin):
    __tablename__ = 'admin'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    date_joined = db.Column(db.DateTime, default=datetime.utcnow)
    role = db.Column(db.String(20), default='admin')  # Added role attribute

    @property
    def is_active(self):
        # Define the condition for an active admin user (e.g., the admin is always active)
        return True
    
    @property
    def is_authenticated(self):  # ✅ Ensure Flask-Login recognizes Admin as logged in
        return True
    
    @property
    def is_admin(self):
        return True
    
    @property
    def user_theme(self):
        return True
    
    def set_theme(self, theme):
        self.user_theme = theme
        db.session.commit()

    def set_password(self, password):
        """Hash the password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify the password."""
        return check_password_hash(self.password_hash, password)
    
    def get_id(self):
        """Return the unique identifier for the admin (used by Flask-Login)."""
        return str(self.id)

#password="kcdg qfpj ibed talg"
class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    price = db.Column(db.Float, nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=False)
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    condition = db.Column(db.String(50), nullable=True)
    brand = db.Column(db.String(150), nullable=True)
    gender = db.Column(db.String(150), nullable=True)
    color = db.Column(db.String(150), nullable=True)
    size = db.Column(db.String(150), nullable=True)
    is_package = db.Column(db.Boolean, nullable=False, default=False)
    qr_code = db.Column(db.String(255), nullable=True)
    view_count = db.Column(db.Integer, default=0)

    # Commission plan association
    commission_plan_id = db.Column(db.Integer, db.ForeignKey('commission_plans.id'))  # Corrected table name
    commission_plans = db.relationship('CommissionPlan', secondary=product_commission, back_populates='products')

    # Relationships
    seller = db.relationship('Seller', backref='products')
    images = db.relationship('ProductImage', backref='associated_product', lazy=True)
    components = db.relationship('ProductComponent', back_populates='product', lazy=True)
    cart_entries = db.relationship('Cart', back_populates='product', lazy=True)

    def increment_view_count(self):
        self.view_count += 1
        db.session.commit()

    def attach_commission_plan(self, commission_plan_id):
        """Attach a commission plan to this product."""
        commission_plan = CommissionPlan.query.get(commission_plan_id)
        if commission_plan and commission_plan not in self.commission_plans:
            self.commission_plans.append(commission_plan)
            db.session.commit()


    @property
    def attached_commission(self):
        """Get the commission rate from the attached commission plan."""
        if self.commission_plan:
            return self.commission_plan.commission_rate
        return None

class Buyer(db.Model):
    __tablename__ = 'buyers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), nullable=False)    

class ProductImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    image_url = db.Column(db.String(200), nullable=False)

class ProductVideo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    video_url = db.Column(db.String(200), nullable=False)

class ProductComponent(db.Model):
    __tablename__ = 'product_component'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    product = db.relationship('Product', back_populates='components')  # Adjusted

class SavedProduct(db.Model):
    __tablename__ = 'saved_products'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    date_saved = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='saved_products')
    product = db.relationship('Product', backref='saved_by_users')

class Cart(db.Model):
    __tablename__ = 'cart'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    component_id = db.Column(db.Integer, db.ForeignKey('product_component.id'))  # Optional link to components
    quantity = db.Column(db.Integer, nullable=False)
    user = db.relationship('User', back_populates='cart_items')
    product = db.relationship('Product', back_populates='cart_entries')
    component = db.relationship('ProductComponent', backref='cart_items')

    @property
    def total_price(self):
        product_price = self.product.price
        if self.component:
            # Add component price if available
            product_price += self.component.price
        return product_price * self.quantity

class Theme(db.Model):
    __tablename__ = 'theme'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    css_file = db.Column(db.String(255), nullable=False)

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('buyers.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    order_date = db.Column(db.DateTime, default=datetime.utcnow)

    buyer = db.relationship('Buyer', backref='orders')
    items = db.relationship('OrderItem', backref='order', lazy=True)

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Float, nullable=False)

    product = db.relationship('Product', backref='order_items')


from sqlalchemy.dialects.postgresql import JSON

class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    price = db.Column(db.Float, nullable=False)
    validity_period = db.Column(db.Integer, nullable=False, default=30)  # Validity in days
    features = db.Column(JSON, nullable=True)  # Stores plan-specific features
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='inactive')  # 'active' or 'inactive'

    # Resolve overlaps
    seller_subscriptions = db.relationship('SellerSubscription', back_populates='subscription', overlaps='sellers,subscriptions')

    def activate(self):
        self.status = 'active'
        db.session.commit()

    def deactivate(self):
        self.status = 'inactive'
        db.session.commit()


# SellerSubscription model
class SellerSubscription(db.Model):
    __tablename__ = 'seller_subscription'
    seller_id = db.Column(db.Integer, db.ForeignKey('sellers.id'), primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id'), primary_key=True)
    subscribed_on = db.Column(db.DateTime, default=datetime.utcnow)
    valid_until = db.Column(db.DateTime, nullable=False)

    subscription = db.relationship('Subscription', back_populates='seller_subscriptions', overlaps='sellers,subscriptions')
    seller = db.relationship('Seller', back_populates='seller_subscriptions', overlaps='subscriptions,sellers')

    @property
    def is_valid(self):
        return self.valid_until >= datetime.utcnow()

    @property
    def time_remaining(self):
        return max(self.valid_until - datetime.utcnow(), timedelta(0))
    

class CommissionPlan(db.Model):
    __tablename__ = 'commission_plans'
    id = db.Column(db.Integer, primary_key=True)
    plan_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    commission_rate = db.Column(db.Float, nullable=False)  # Rate as a percentage
    seller_id = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=False)  # Corrected to link to sellers.id
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # Plan activation status

    # Relationship to Seller
    seller = db.relationship('Seller', backref='commission_plans', lazy=True)
    # Many-to-Many relationship with Product
    products = db.relationship('Product', secondary=product_commission, back_populates='commission_plans')

    def activate(self):
        self.is_active = True
        db.session.commit()

    def deactivate(self):
        self.is_active = False
        db.session.commit()


# Seller model
class Seller(db.Model):
    __tablename__ = 'sellers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(120), nullable=True)
    user = db.relationship('User', backref='seller_relationship', uselist=False)

    # Resolve overlaps
    seller_subscriptions = db.relationship('SellerSubscription', back_populates='seller', overlaps='subscriptions,sellers')

    def subscribe_to(self, subscription_id, validity_period):
        # Check if the subscription exists
        subscription = Subscription.query.get(subscription_id)
        if not subscription:
            raise ValueError("Subscription not found.")

        # Check if the subscription is active
        if subscription.status != 'active':
            raise ValueError("Subscription is not active.")

        # Create or update the SellerSubscription record
        seller_subscription = SellerSubscription.query.filter_by(
            seller_id=self.id, subscription_id=subscription_id).first()

        if seller_subscription:
            # Update validity if subscription exists
            seller_subscription.valid_until = datetime.utcnow() + timedelta(days=validity_period)
        else:
            # Add a new subscription
            new_subscription = SellerSubscription(
                seller_id=self.id,
                subscription_id=subscription_id,
                valid_until=datetime.utcnow() + timedelta(days=validity_period)
            )
            db.session.add(new_subscription)

        # Commit changes
        db.session.commit()

    def unsubscribe_from(self, subscription_id):
        seller_subscription = SellerSubscription.query.filter_by(
            seller_id=self.id,
            subscription_id=subscription_id
        ).first()
        if seller_subscription:
            db.session.delete(seller_subscription)
            db.session.commit()


#--------AFFILIATES-----------------------
class Affiliate(db.Model):
    __tablename__ = 'affiliates'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    referral_code = db.Column(db.String(8), unique=True, nullable=False)
    earnings = db.Column(db.Float, default=0.0)
    username = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    id_type = db.Column(db.String(50), nullable=True)
    id_front_image = db.Column(db.String(255), nullable=True)
    id_back_image = db.Column(db.String(255), nullable=True)

    # Updated relationship to resolve overlap issues
    user = db.relationship('User', back_populates='affiliate_account', overlaps="affiliate_account_ref")

class Referral(db.Model):
    __tablename__ = 'referrals'
    id = db.Column(db.Integer, primary_key=True)
    affiliate_id = db.Column(db.Integer, db.ForeignKey('affiliates.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    commission = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='pending')  # Add this line

    affiliate = db.relationship('Affiliate', backref='referrals')
    product = db.relationship('Product', backref='referrals')
    order = db.relationship('Order', backref='referral')

class AffiliateSignup(db.Model):
    __tablename__ = 'affiliate_signups'
    id = db.Column(db.Integer, primary_key=True)
    affiliate_id = db.Column(db.Integer, db.ForeignKey('affiliates.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    affiliate = db.relationship('Affiliate', backref='signups')
    user = db.relationship('User', backref='affiliate_signup')

#--------------COMMUNICATION----------------
class Conversation(db.Model):
    __tablename__ = 'conversation'
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Buyer
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Seller
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)  # The product being discussed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship('Message', back_populates='conversation', cascade='all, delete-orphan')
    buyer = db.relationship('User', foreign_keys=[buyer_id], backref='buyer_conversations')
    seller = db.relationship('User', foreign_keys=[seller_id], backref='seller_conversations')
    product = db.relationship('Product', backref='conversations')

class Message(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Sender can be buyer or seller
    sender_role = db.Column(db.String(10), nullable=False)  # 'buyer' or 'seller'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)  # Tracks if the message is read

    conversation = db.relationship('Conversation', back_populates='messages')
    sender = db.relationship('User', backref='messages', foreign_keys=[sender_id])
