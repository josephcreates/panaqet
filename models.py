from decimal import Decimal
import uuid
from flask_login import UserMixin
from flask import url_for
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Column, Integer, Numeric, String, ForeignKey, DateTime, Boolean, Text, Float, Table, UniqueConstraint
from sqlalchemy.orm import relationship, backref
from datetime import datetime, timedelta
from database import db


# Association table for the many-to-many relationship between Product and CommissionPlan
product_commission = Table(
    'product_commission',
    db.metadata,  # Use Flask-SQLAlchemy's metadata
    db.Column('product_id', db.Integer, db.ForeignKey('product.id'), primary_key=True),
    db.Column('commission_plan_id', db.Integer, db.ForeignKey('commission_plans.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    email = db.Column(db.String(120), index=True, unique=True, nullable=False)
    country_code = db.Column(db.String(5), nullable=True)
    phone_number = db.Column(db.String(15), unique=True, nullable=True)
    country = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(300))
    role = db.Column(db.String(20))
    date_joined = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    profile_image = db.Column(db.String(255), nullable=False, default='default_profile.png')
    
    # Store / seller related
    store_name = db.Column(db.String(100), nullable=True)
    store_description = db.Column(db.String(255), nullable=True)
    store_logo = db.Column(db.String(255), nullable=True)
    
    # Preferences
    user_theme = db.Column(db.String(50), default='light')
    preferred_language = db.Column(db.String(10), default='en')
    
    # Identity documents
    id_type = db.Column(db.String(50), nullable=True)
    id_front_image = db.Column(db.String(255), nullable=True)
    id_back_image = db.Column(db.String(255), nullable=True)
    
    # Account flags
    signup_complete = db.Column(db.Boolean, default=False)
    is_first_login = db.Column(db.Boolean, default=True)
    
    # Relationships
    cart_items = db.relationship('Cart', back_populates='user', lazy=True)
    saved_products = db.relationship('SavedProduct', back_populates='user', lazy=True)
    affiliate_account = db.relationship('Affiliate', back_populates='user', uselist=False)
    buyer_profile = db.relationship('Buyer', back_populates='user', uselist=False)
    reviews = db.relationship('ProductReview', back_populates='buyer', lazy=True)

    @property
    def is_seller(self):
        return self.role == 'seller'
    
    @property
    def is_buyer(self):
        return self.role == 'buyer'
    
    @property
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def is_affiliate(self):
        return self.role == 'affiliate'
    
    # Theme methods
    def set_theme(self, theme):
        self.user_theme = theme
        db.session.commit()
    
    def get_theme(self):
        return self.user_theme
    
    # Password methods
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    # Language methods
    def set_language(self, language):
        self.preferred_language = language
        db.session.commit()
    
    def get_language(self):
        return self.preferred_language
    
    # Update location (also updates Seller table if applicable)
    def update_location(self, country, location, lat=None, lng=None):
        self.country = country
        self.location = location
        db.session.commit()

        if self.is_seller:
            seller = Seller.query.filter_by(user_id=self.id).first()
            if seller:
                seller.country = country
                seller.location = location
                seller.lat = lat
                seller.lng = lng
                db.session.add(seller)
                db.session.commit()

    # User creation hook
    @classmethod
    def create_user(cls, username, email, password, role, country, location=None, lat=None, lng=None, phone_number=None):
        user = cls(username=username, email=email, role=role, country=country, phone_number=phone_number, location=location)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        if user.is_seller:
            seller = Seller( ... ) 
            db.session.add(seller)
        else:
            buyer = Buyer(...)
            db.session.add(buyer)

        db.session.commit()

        # create wallet with currency derived from country
        from wallet import country_to_currency, get_or_create_wallet
        currency = country_to_currency(country, fallback='USD')
        get_or_create_wallet(user.id, currency=currency, name=f"{user.username}'s Wallet")

        return user

    @property
    def profile_url(self):
        path = (self.profile_image or '').replace('\\', '/').lstrip('/')
        if path.startswith('static/'):
            path = path[len('static/'):]
        return url_for('static', filename=path)
    

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


# Seller model
class Seller(db.Model):
    __tablename__ = 'sellers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(120), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)

    user = db.relationship('User', backref=db.backref('seller_relationship', uselist=False), uselist=False)
    products = db.relationship('Product', back_populates='seller', lazy=True)
    conversations = db.relationship('Conversation', back_populates='seller', lazy=True)
    seller_subscriptions = db.relationship('SellerSubscription', back_populates='seller', overlaps='subscriptions,sellers')
    subscriptions = db.relationship('Subscription', secondary='seller_subscription', back_populates='sellers', overlaps='seller_subscriptions')
    
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
    
    def get_affiliates(self):
        """Return all affiliates who have promoted any of this seller's products."""
        from models import Referral, Affiliate, Product
        affiliate_ids = (
            db.session.query(Referral.affiliate_id)
            .join(Product, Product.id == Referral.product_id)
            .filter(Product.seller_id == self.id)
            .distinct()
            .all()
        )
        affiliate_ids = [a[0] for a in affiliate_ids]
        return Affiliate.query.filter(Affiliate.id.in_(affiliate_ids)).all()


class Buyer(db.Model):
    __tablename__ = 'buyers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    
    # Optional: store username/email redundantly for convenience
    username = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), nullable=False)

    # Relationship to User
    user = db.relationship('User', back_populates='buyer_profile', uselist=False)

    # Conversations where this buyer is involved
    conversations = db.relationship('Conversation', back_populates='buyer', lazy=True)

    # Orders
    orders = db.relationship('Order', back_populates='buyer', lazy=True)

    def __repr__(self):
        return f"<Buyer {self.username} ({self.email})>"

#-------------------PRODUCTS---------------------
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
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    location = db.Column(db.String(200), nullable=True)
    condition = db.Column(db.String(50), nullable=True)
    brand = db.Column(db.String(150), nullable=True)
    gender = db.Column(db.String(150), nullable=True)
    color = db.Column(db.String(150), nullable=True)
    size = db.Column(db.String(150), nullable=True)
    is_package = db.Column(db.Boolean, nullable=False, default=False)
    qr_code = db.Column(db.String(255), nullable=True)
    view_count = db.Column(db.Integer, default=0)

    # One-to-many: product belongs to one commission plan
    commission_plan_id = db.Column(db.Integer, db.ForeignKey('commission_plans.id'))
    commission_plan = db.relationship('CommissionPlan', back_populates='products')

    # Relationships
    seller = db.relationship('Seller', back_populates='products')
    category = db.relationship('Category', backref='products')
    images = db.relationship('ProductImage', back_populates='associated_product', lazy=True)
    videos = db.relationship('ProductVideo', backref='product', lazy=True)
    components = db.relationship('ProductComponent', back_populates='product', lazy=True)
    cart_entries = db.relationship('Cart', back_populates='product', lazy=True)
    saved_by_users = db.relationship('SavedProduct', back_populates='product', lazy=True)
    conversations = db.relationship('Conversation', back_populates='product', lazy=True)
    reviews = db.relationship('ProductReview', back_populates='product', lazy=True)

    def increment_view_count(self):
        self.view_count += 1
        db.session.commit()

    def attach_commission_plan(self, commission_plan_id):
        """Attach or replace the commission plan for this product."""
        self.commission_plan_id = commission_plan_id
        if self.price and self.commission_plan:
            self.commission = (self.commission_plan.commission_rate / 100.0) * self.price
        else:
            self.commission = None
        db.session.commit()

    @property
    def attached_commission(self):
        return self.commission_plan.commission_rate if self.commission_plan else None
    
    @property
    def display_image_url(self):
        """Return a usable image URL for the product (first image or default)."""
        # prefer ProductImage.url property if available
        if self.images:
            try:
                # ProductImage.url returns a full url_for('static', ...)
                return self.images[0].url
            except Exception:
                # fallback if something odd happens with url_for
                path = (self.images[0].image_url or '').replace('\\', '/').lstrip('/')
                return url_for('static', filename=path)
        # default product image (put a default_image.jpg in your static folder)
        return url_for('static', filename='default_image.jpg')

    @property
    def average_rating(self):
        if not self.reviews:
            return None
        return round(sum(r.rating for r in self.reviews) / len(self.reviews), 1)

    @property
    def review_count(self):
        return len(self.reviews)

class ProductReview(db.Model):
    __tablename__ = 'product_reviews'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5 stars
    comment = db.Column(db.String(500), nullable=True)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    # Ensure one review per buyer per product
    __table_args__ = (db.UniqueConstraint('product_id', 'buyer_id', name='uix_product_buyer_review'),)

    # Relationships
    product = db.relationship('Product', back_populates='reviews')
    buyer = db.relationship('User', back_populates='reviews')

    @property
    def average_rating(self):
        if not self.reviews:
            return None
        return sum(r.rating for r in self.reviews) / len(self.reviews)

class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('categories.id', ondelete='CASCADE'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship to access children
    children = db.relationship(
        'Category',
        backref=db.backref('parent', remote_side=[id]),
        cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f"<Category {self.name}>"
                           
class ProductImage(db.Model):
    __tablename__ = 'product_image'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    image_url = db.Column(db.String(200), nullable=False)  # stored path
    associated_product = db.relationship('Product', back_populates='images')

    @property
    def url(self):
        path = (self.image_url or '').replace('\\', '/')  # normalize
        path = path.lstrip('/')                          # remove leading slashes
        if path.startswith('static/'):
            path = path[len('static/'):]                 # strip duplicate
        return url_for('static', filename=path)

class ProductVideo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    video_url = db.Column(db.String(200), nullable=False)

    @property
    def url(self):
        path = (self.video_url or '').replace('\\', '/').lstrip('/')
        if path.startswith('static/'):
            path = path[len('static/'):]
        return url_for('static', filename=path)

class ProductComponent(db.Model):
    __tablename__ = 'product_component'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(255), nullable=True)   # new column
    product = db.relationship('Product', back_populates='components')

class SavedProduct(db.Model):
    __tablename__ = 'saved_products'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    date_saved = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='saved_products')
    product = db.relationship('Product', back_populates='saved_by_users')  # ✅ Fix

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
    

#-------------------THEMES---------------------
class Theme(db.Model):
    __tablename__ = 'theme'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    css_file = db.Column(db.String(255), nullable=False)


#-------------------ORDERS---------------------
class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('buyers.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    # inside Order model
    shipping_name = db.Column(db.String(120))
    shipping_address = db.Column(db.String(255))
    latitude = db.Column(db.String(50))
    longitude = db.Column(db.String(50))
    payment_method = db.Column(db.String(50))
    latitude = db.Column(db.String(50))   # option: change to Float
    longitude = db.Column(db.String(50))  # option: change to Floa
    # overall order status (reflects the aggregate of its items)
    status = db.Column(db.String(32), nullable=False, default='Pending')  # Pending, Partially Approved, Approved, Declined, Shipped, Delivered
    affiliate_id = db.Column(db.Integer, db.ForeignKey('affiliates.id'), nullable=True)
    affiliate_commission_amount = db.Column(Numeric(12,2), nullable=True)
    buyer_discount_amount = db.Column(Numeric(12,2), nullable=True)
    
    affiliate = db.relationship('Affiliate', backref='orders')
    buyer = db.relationship('Buyer', back_populates='orders')
    items = db.relationship('OrderItem', backref='order', lazy=True)

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Float, nullable=False)

    # NEW: per-item status (Pending, Approved, Declined)
    status = db.Column(db.String(32), nullable=False, default='Pending')

    product = db.relationship('Product', backref='order_items')

#-------------------DELIVERIES---------------------
class Driver(db.Model, UserMixin):  # Add UserMixin here
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    license_number = db.Column(db.String(50), unique=True, nullable=False)
    vehicle_type = db.Column(db.String(50))
    vehicle_number = db.Column(db.String(50))
    status = db.Column(db.String(20), default="Pending")  # Pending, Approved, Suspended
    password_hash = db.Column(db.String(200))
    date_joined = db.Column(db.DateTime, default=datetime.utcnow)

    deliveries = db.relationship('Delivery', backref='driver', lazy=True)

# models.py (or wherever Delivery is defined)
class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    buyer_id = db.Column(db.Integer, nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=True)
    pickup_location = db.Column(db.String(200))
    dropoff_location = db.Column(db.String(200))
    distance_km = db.Column(db.Float)
    estimated_cost = db.Column(db.Float)
    status = db.Column(db.String(30), default="Pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    pickup_lat = db.Column(db.Float, nullable=True)
    pickup_lng = db.Column(db.Float, nullable=True)
    dropoff_lat = db.Column(db.Float, nullable=True)
    dropoff_lng = db.Column(db.Float, nullable=True)
    route_coords = db.Column(db.Text)  # store JSON string of [[lng, lat], ...]
    
    order = db.relationship('Order', backref='deliveries')

class DriverLocation(db.Model):
    __tablename__ = 'driver_location'
    driver_id = db.Column(db.Integer, primary_key=True)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    delivery_id = db.Column(db.Integer, nullable=True)  # currently assigned delivery (optional)
    name = db.Column(db.String(120), nullable=True)

#-------------------SUBSCRIPTIONS---------------------
from sqlalchemy.dialects.postgresql import JSON

class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    price = db.Column(db.Float, nullable=False)
    validity_period = db.Column(db.Integer, nullable=False, default=30)  # days
    features = db.Column(JSON, nullable=True)
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='inactive')

    # association relationship
    seller_subscriptions = db.relationship('SellerSubscription', back_populates='subscription', overlaps='sellers,subscriptions')

    # convenience many-to-many relationship (reads/writes go through SellerSubscription)
    sellers = db.relationship('Seller', secondary='seller_subscription', back_populates='subscriptions', overlaps='seller_subscriptions')

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
    commission_rate = db.Column(db.Float, nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    seller = db.relationship('Seller', backref='commission_plans', lazy=True)
    products = db.relationship('Product', back_populates='commission_plan')

    def activate(self):
        self.is_active = True
        db.session.commit()

    def deactivate(self):
        self.is_active = False
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

#-------------------AFFILIATE COMMISSIONS & SIGNUPS---------------------
class AffiliateSignup(db.Model):
    __tablename__ = 'affiliate_signups'
    id = db.Column(db.Integer, primary_key=True)
    affiliate_id = db.Column(db.Integer, db.ForeignKey('affiliates.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    reward_credited = db.Column(db.Boolean, default=False)   # <- new
    reward_txn_id = db.Column(db.Integer, nullable=True)     # optional pointer to WalletTransaction.id

    affiliate = db.relationship('Affiliate', backref='signups')
    user = db.relationship('User', backref='affiliate_signup')

class AffiliateCommissionPlan(db.Model):
    __tablename__ = 'affiliate_commission_plans'
    id = db.Column(db.Integer, primary_key=True)
    affiliate_id = db.Column(db.Integer, db.ForeignKey('affiliates.id'), nullable=False)
    buyer_discount = db.Column(db.Float, default=5.0)  # % discount for buyers
    commission_percent = db.Column(db.Float, default=10.0)  # % of order amount for affiliate
    is_active = db.Column(db.Boolean, default=True)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

    affiliate = db.relationship('Affiliate', backref='commission_plans')

class CommissionSettings(db.Model):
    __tablename__ = 'commission_settings'
    id = db.Column(db.Integer, primary_key=True)
    # If None => no global override, use seller plans
    global_affiliate_percent = db.Column(db.Float, nullable=True)
    # discount applied to buyer who used affiliate (percentage, e.g. 5.0)
    affiliate_buyer_discount_percent = db.Column(db.Float, nullable=True)
    # signup reward (flat currency amount) given to affiliate on successful signup (optional)
    affiliate_signup_reward = db.Column(db.Float, nullable=True)
    # feature toggle: when True, admin control is enforced
    admin_control_enabled = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ------------------------------------------------------------
# COMMUNICATION (Chat) Models
# For conversations and messages, we now reference the Buyers and Sellers tables.
# ------------------------------------------------------------
class Conversation(db.Model):
    __tablename__ = 'conversation'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('buyers.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('sellers.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    messages = db.relationship('Message', back_populates='conversation', cascade='all, delete-orphan', lazy=True)
    product = db.relationship('Product', back_populates='conversations', lazy=True)
    buyer = db.relationship('Buyer', back_populates='conversations', lazy=True)
    seller = db.relationship('Seller', back_populates='conversations', lazy=True)

class Message(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sender_role = db.Column(db.String(10), nullable=False)  # e.g. 'buyer' or 'seller'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    conversation = db.relationship('Conversation', back_populates='messages')
    sender = db.relationship('User', backref='sent_messages', lazy=True)


#-------------------WALLET & TRANSACTIONS---------------------
class Wallet(db.Model):
    __tablename__ = "wallets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    qr_code_id = db.Column(db.String(64), unique=True, default=lambda: str(uuid.uuid4()))
    balance = db.Column(Numeric(12,2), default=Decimal('0.00'), nullable=False)
    currency = db.Column(db.String(8), default='GHS', nullable=False)
    name = db.Column(db.String(100), default="Main Wallet")  # ✅ add this line
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('wallet', uselist=False))

    def __repr__(self):
        return f"<Wallet user={self.user_id} balance={self.balance}>"

class WalletTransaction(db.Model):
    __tablename__ = "wallet_transactions"
    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallets.id'), nullable=False)
    amount = db.Column(Numeric(12,2), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    source = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(32), nullable=False, default='pending')
    reference_type = db.Column(db.String(50), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)  # <--- rename here

    wallet = db.relationship('Wallet', backref='transactions')

    def __repr__(self):
        return f"<WalletTxn {self.id} wallet={self.wallet_id} amt={self.amount} src={self.source} status={self.status}>"

#-------------------EXCHANGE RATES---------------------
class ExchangeRate(db.Model):
    __tablename__ = 'exchange_rates'
    id = db.Column(db.Integer, primary_key=True)
    from_currency = db.Column(db.String(8), nullable=False)
    to_currency = db.Column(db.String(8), nullable=False)
    rate = db.Column(Numeric(12,6), nullable=False)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('from_currency', 'to_currency', name='_unique_rate_pair'),)

    def __repr__(self):
        return f"<ExchangeRate {self.from_currency}->{self.to_currency}={self.rate}>"

class SystemConfig(db.Model):
    __tablename__ = "system_config"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)

    @staticmethod
    def get_value(key, default=None):
        cfg = SystemConfig.query.filter_by(key=key).first()
        return cfg.value if cfg else default

    @staticmethod
    def set_value(key, value):
        cfg = SystemConfig.query.filter_by(key=key).first()
        if not cfg:
            cfg = SystemConfig(key=key, value=value)
            db.session.add(cfg)
        else:
            cfg.value = value
        db.session.commit()
