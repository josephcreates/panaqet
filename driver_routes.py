# driver_routes.py
import os
import logging
import time
from datetime import datetime
from typing import Optional, List

import requests
import googlemaps
import polyline as polyline_lib

from flask import Blueprint, abort, current_app, request, jsonify, render_template, flash, redirect, session, url_for, json
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import joinedload

from database import db
from forms import DriverLoginForm, DriverRegistrationForm
from geocode import geocode_address  # local fallback geocoder (kept for resilience)
# NOTE: we intentionally DO NOT import graphml.get_route_on_roads here — routing is via microservice
from models import Buyer, Driver, Delivery, DriverLocation, Order, Product, Seller, User
from extensions import get_gmaps_client
from utils.live_location_client import send_driver_location_to_live_service

driver_bp = Blueprint('driver', __name__, url_prefix='/driver')
LOG = logging.getLogger(__name__)

# Reuse a requests.Session for connection pooling to the routing service
_requests_sess = requests.Session()

# default timeouts (seconds)
ROUTING_TIMEOUT = 30
GEOCODE_TIMEOUT = 10

# helper to read routing service base URL from Flask config (fallback to localhost:8010)
def _routing_base_url():
    return current_app.config.get('ROUTING_SERVICE_URL', 'http://localhost:8010').rstrip('/')

# Replace existing emit_socket(...) implementation with this
def emit_socket(event_name: str, payload: dict, room: str = None, namespace: str = '/'):
    """
    Emit using whatever socket interface is available on current_app.
    This tries common signatures and falls back gracefully if unsupported.
    """
    try:
        sock = getattr(current_app, 'socketio', None) or current_app.extensions.get('socketio')
        if not sock:
            current_app.logger.debug("SocketIO not configured; skipping emit %s", event_name)
            return

        # Try the common signatures in order to support different socketio versions:
        # 1) flask-socketio: emit(event, data, room=room, namespace=namespace, broadcast=...)
        # 2) python-socketio server: emit(event, data, to=room, namespace=namespace)
        # 3) fallback: emit(event, data, namespace=namespace)
        try:
            if room:
                # try python-socketio style (to=room)
                sock.emit(event_name, payload, to=room, namespace=namespace)
            else:
                # try without room
                sock.emit(event_name, payload, namespace=namespace)
        except TypeError as e:
            # maybe the library expects broadcast kw; try flask-socketio style
            try:
                if room:
                    sock.emit(event_name, payload, room=room, namespace=namespace)
                else:
                    sock.emit(event_name, payload, broadcast=True, namespace=namespace)
            except Exception as e2:
                current_app.logger.debug("Socket emit second attempt failed for %s: %s", event_name, e2)
    except Exception as e:
        current_app.logger.debug("Socket emit failed for %s: %s", event_name, e)


# -------------------------------DRIVER AUTHENTICATION  -------------------------------
@driver_bp.route('/register', methods=['GET', 'POST'])
def register_driver():
    form = DriverRegistrationForm()
    if form.validate_on_submit():
        full_name = form.full_name.data
        phone = form.phone.data
        email = form.email.data
        license_number = form.license_number.data
        vehicle_type = form.vehicle_type.data
        vehicle_number = form.vehicle_number.data
        password = form.password.data

        if Driver.query.filter_by(email=email).first():
            flash("Email already in use", "danger")
            return redirect(url_for('driver.register_driver'))
        if Driver.query.filter_by(license_number=license_number).first():
            flash("License number already in use", "danger")
            return redirect(url_for('driver.register_driver'))

        driver = Driver(
            full_name=full_name,
            phone=phone,
            email=email,
            license_number=license_number,
            vehicle_type=vehicle_type,
            vehicle_number=vehicle_number,
            password_hash=generate_password_hash(password)
        )
        db.session.add(driver)
        db.session.commit()
        flash("Driver registered successfully! Please log in.", "success")
        return redirect(url_for('driver.login_driver'))

    return render_template('driver/driver_register.html', form=form)


@driver_bp.route('/login', methods=['GET', 'POST'])
def login_driver():
    form = DriverLoginForm()
    if form.validate_on_submit():
        driver = Driver.query.filter_by(email=form.email.data).first()
        if driver and check_password_hash(driver.password_hash, form.password.data):
            # Mark this as a driver session
            session['user_type'] = 'driver'
            login_user(driver)
            flash("Logged in successfully!", "success")
            return redirect(url_for('driver.driver_map'))
        else:
            flash("Invalid email or password", "danger")
    return render_template('driver/driver_login.html', form=form)


@driver_bp.route('/logout', methods=['POST'])
@login_required
def logout_driver():
    logout_user()
    return redirect(url_for('driver.login_driver'))  

# -------------------------------
# DRIVER DELIVERIES
# -------------------------------
@driver_bp.route('/delivery/<int:delivery_id>')
@login_required
def delivery_overview(delivery_id):
    delivery = Delivery.query.get_or_404(delivery_id)
    if delivery.driver_id != current_user.id:
        abort(403)
    order = Order.query.get(delivery.order_id) if delivery.order_id else None
    return render_template('driver/delivery_overview.html', delivery=delivery, order=order)


@driver_bp.route('/deliveries/page', methods=['GET'])
@login_required
def deliveries_page():
    deliveries = Delivery.query.filter_by(driver_id=current_user.id).all()
    return render_template('driver/driver_deliveries.html', deliveries=deliveries)


@driver_bp.route('/deliveries/<int:delivery_id>/update', methods=['POST'])
@login_required
def update_delivery_status(delivery_id):
    delivery = Delivery.query.get_or_404(delivery_id)

    if delivery.driver_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for('driver.deliveries_page'))

    new_status = request.form.get('status')
    if new_status in ["Pending", "In Transit", "Delivered"]:
        delivery.status = new_status
        db.session.commit()
        emit_socket('delivery_status_changed', {
            'delivery_id': delivery.id,
            'status': delivery.status,
            'driver_id': delivery.driver_id
        })
        flash(f"Delivery status updated to {new_status}", "success")
    else:
        flash("Invalid status", "danger")

    return redirect(url_for('driver.deliveries_page'))


# -------------------------------
# DRIVER LOCATION TRACKING
# -------------------------------
@driver_bp.route('/location/update', methods=['POST'])
@login_required
def update_driver_location():
    data = request.json or {}
    lat = data.get('lat')
    lng = data.get('lng')
    delivery_id = data.get('delivery_id')  # can be None

    if lat is None or lng is None:
        return jsonify({'error': 'Missing coordinates'}), 400

    # Update local DB
    driver_loc = DriverLocation.query.filter_by(driver_id=current_user.id).first()
    if not driver_loc:
        driver_loc = DriverLocation(driver_id=current_user.id)

    driver_loc.lat = lat
    driver_loc.lng = lng
    driver_loc.delivery_id = delivery_id
    driver_loc.updated_at = datetime.utcnow()
    db.session.add(driver_loc)
    db.session.commit()

    # Send to the Go live location service
    send_driver_location_to_live_service(current_user.id, lat, lng)

    # Emit for real-time frontend updates (Flask Socket)
    emit_socket('driver_location_update', {
        'driver_id': current_user.id,
        'lat': driver_loc.lat,
        'lng': driver_loc.lng,
        'delivery_id': driver_loc.delivery_id,
        'updated_at': driver_loc.updated_at.isoformat()
    })

    return jsonify({'message': 'Location updated successfully'})

def get_all_driver_locations():
    """
    Returns all drivers with their current coordinates, even if idle
    """
    drivers = DriverLocation.query.all()
    locs = []
    for d in drivers:
        if d.lat is not None and d.lng is not None:
            locs.append({
                "driver_id": d.driver_id,
                "lat": float(d.lat),
                "lng": float(d.lng),
                "delivery_id": d.delivery_id,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None
            })
    return locs

@driver_bp.route('/location', methods=['GET'])
@login_required
def get_driver_location():
    driver_loc = DriverLocation.query.filter_by(driver_id=current_user.id).first()
    if not driver_loc:
        return jsonify({'lat': None, 'lng': None, 'delivery_id': None, 'updated_at': None})
    return jsonify({
        'lat': driver_loc.lat,
        'lng': driver_loc.lng,
        'delivery_id': driver_loc.delivery_id,
        'updated_at': driver_loc.updated_at.isoformat() if driver_loc.updated_at else None
    })


# -------------------------------
# AVAILABLE DELIVERIES (UNASSIGNED)
# -------------------------------
@driver_bp.route('/available/page', methods=['GET'])
@login_required
def available_deliveries_page():
    deliveries = Delivery.query.join(Order, Delivery.order_id == Order.id).filter(
        Delivery.driver_id.is_(None),
        Order.status == 'Approved'
    ).all()

    enriched = []
    for d in deliveries:
        order = Order.query.get(d.order_id)
        seller = None
        if order and getattr(order, 'items', None):
            first_prod = Product.query.get(order.items[0].product_id)
            if first_prod:
                seller = Seller.query.get(first_prod.seller_id)
        buyer = User.query.get(d.buyer_id) if d.buyer_id else None

        enriched.append({
            'delivery': d,
            'seller': seller,
            'buyer': buyer,
        })

    return render_template('driver/available_deliveries.html', deliveries=enriched)


@driver_bp.route('/deliveries/<int:delivery_id>/accept', methods=['POST'])
@login_required
def accept_delivery(delivery_id):
    delivery = Delivery.query.get_or_404(delivery_id)

    # If someone already assigned this delivery
    if delivery.driver_id:
        msg = "Delivery already assigned."
        is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                 'application/json' in request.headers.get('Accept', '')
        if is_xhr:
            return jsonify({'ok': False, 'error': msg}), 400
        flash(msg, "danger")
        return redirect(url_for('driver.available_deliveries_page'))

    # --- NEW: prevent driver from having more than one active delivery ---
    active = Delivery.query.filter(
        Delivery.driver_id == current_user.id,
        Delivery.status == 'In Transit'
    ).first()
    if active:
        msg = "You already have an active delivery. Complete it before accepting another."
        is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                 'application/json' in request.headers.get('Accept', '')
        if is_xhr:
            return jsonify({'ok': False, 'error': msg, 'active_delivery_id': active.id}), 400
        flash(msg, "warning")
        return redirect(url_for('driver.map'))

    try:
        # assign
        delivery.driver_id = current_user.id
        delivery.status = "In Transit"
        db.session.add(delivery)
        # optional: set started_at = datetime.utcnow() if you have that column
        db.session.commit()

        # (rest of your route generation + emits follow the same as before)
        try:
            driver_loc = DriverLocation.query.filter_by(driver_id=current_user.id).first()
            if driver_loc and delivery.pickup_lat and delivery.pickup_lng:
                pickup = {'lat': float(driver_loc.lat), 'lng': float(driver_loc.lng)}
                dropoff = {'lat': float(delivery.pickup_lat), 'lng': float(delivery.pickup_lng)}
                route_data = get_route_from_service(pickup, dropoff, alternatives=1)
                if route_data:
                    emit_socket('driver_pickup_route', {
                        'delivery_id': delivery.id,
                        'driver_id': current_user.id,
                        'pickup_location': delivery.pickup_location,
                        'route': route_data
                    }, room=f"driver_{current_user.id}")
        except Exception as e:
            current_app.logger.exception(
                "Failed to compute route to pickup for delivery %s: %s", delivery_id, e
            )

        payload = {
            'id': delivery.id,
            'order_id': delivery.order_id,
            'pickup_lat': delivery.pickup_lat,
            'pickup_lng': delivery.pickup_lng,
            'dropoff_lat': delivery.dropoff_lat,
            'dropoff_lng': delivery.dropoff_lng,
            'pickup_location': delivery.pickup_location,
            'dropoff_location': delivery.dropoff_location,
            'status': delivery.status,
            'driver_id': delivery.driver_id
        }

        socketio = getattr(current_app, 'socketio', None) or current_app.extensions.get('socketio')
        if socketio:
            socketio.emit('delivery_assigned', payload, to=None)
        else:
            current_app.logger.debug('SocketIO not configured; skipping emit delivery_assigned')

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error assigning delivery %s", delivery_id)
        is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                 'application/json' in request.headers.get('Accept', '')
        if is_xhr:
            return jsonify({'ok': False, 'error': 'Database error'}), 500
        flash("Could not accept delivery. Try again.", "danger")
        return redirect(url_for('driver.available_deliveries_page'))

    is_xhr = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
             'application/json' in request.headers.get('Accept', '')
    if is_xhr:
        return jsonify({'ok': True, 'delivery_id': delivery.id, 'order_id': delivery.order_id,
                        'pickup_location': delivery.pickup_location, 'dropoff_location': delivery.dropoff_location})

    flash(f"Delivery {delivery.order_id} accepted.", "success")
    return redirect(url_for('driver.available_deliveries_page'))

@driver_bp.route("/deliveries/<int:delivery_id>/start", methods=["POST"])
@login_required
def start_delivery(delivery_id):
    delivery = Delivery.query.get_or_404(delivery_id)
    if delivery.driver_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403
    if delivery.status != "Accepted":
        return jsonify({"error": "Cannot start delivery in current state"}), 400

    delivery.status = "In Transit"  # triggers buyer/seller tracking visibility
    db.session.commit()
    return jsonify({"success": True, "msg": "Delivery started"})


# helper: build a lightweight serializable list of point locations for the driver map
def build_driver_map_locations(driver_id=None):
    deliveries = Delivery.query.filter(
        ((Delivery.driver_id == driver_id) | (Delivery.driver_id == None)) &
        (Delivery.status != "Delivered")
    ).all()

    locs = []
    for d in deliveries:
        if d.dropoff_lat is not None and d.dropoff_lng is not None:
            locs.append({
                "name": (d.dropoff_location or f"Order {d.order_id}"),
                "lat": float(d.dropoff_lat),
                "lon": float(d.dropoff_lng),
                "type": "dropoff",
                "order_id": d.order_id,
                "delivery_id": d.id
            })
        elif d.pickup_lat is not None and d.pickup_lng is not None:
            locs.append({
                "name": (d.pickup_location or f"Order {d.order_id}"),
                "lat": float(d.pickup_lat),
                "lon": float(d.pickup_lng),
                "type": "pickup",
                "order_id": d.order_id,
                "delivery_id": d.id
            })
    return locs


@driver_bp.route('/map')
@login_required
def driver_map():
    api_key = current_app.config.get('GOOGLE_MAPS_API_KEY') or ''
    driver_locations = get_all_driver_locations()       # 👈 all drivers
    driver = current_user
    other_locations = build_driver_map_locations(current_user.id)  # deliveries

    return render_template('driver/driver_map.html',
                           google_maps_api_key=api_key,
                           driver_locations=driver_locations,
                           other_locations=other_locations,
                           driver=driver)


def get_gmaps_client():
    key = current_app.config.get('GOOGLE_MAPS_API_KEY')
    if not key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not configured")
    return googlemaps.Client(key=key)

# -------------------------
# Helper: call routing microservice
# -------------------------
def get_route_from_service(pickup: dict, dropoff: dict, alternatives: int = 1) -> Optional[dict]:
    """
    Calls FastAPI routing service /route endpoint.
    Expects pickup/dropoff as {'lat':..., 'lng':...}
    Returns route dict on success, or None on failure.
    """
    base = _routing_base_url()
    url = f"{base}/route"
    params = {
        "pickup_lat": float(pickup['lat']),
        "pickup_lng": float(pickup['lng']),
        "drop_lat": float(dropoff['lat']),
        "drop_lng": float(dropoff['lng']),
        "alternatives": int(alternatives)
    }
    try:
        resp = _requests_sess.get(url, params=params, timeout=ROUTING_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        else:
            current_app.logger.warning("Routing service returned %s: %s", resp.status_code, resp.text)
            return None
    except requests.RequestException as exc:
        current_app.logger.warning("Routing service request failed: %s", exc)
        return None


def geocode_via_service(address: str, country_codes: Optional[List[str]] = None) -> Optional[tuple]:
    """
    Call FastAPI /geocode (POST) with {"address": "...", "country_codes": [...]}
    Returns (lat, lng) or None on failure. Falls back to local geocode_address on failure.
    """
    base = _routing_base_url()
    url = f"{base}/geocode"
    payload = {"address": address}
    if country_codes:
        payload["country_codes"] = country_codes
    try:
        resp = _requests_sess.post(url, json=payload, timeout=GEOCODE_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            lat = data.get("lat")
            lng = data.get("lng")
            if lat is not None and lng is not None:
                return float(lat), float(lng)
        else:
            current_app.logger.debug("Geocode service returned %s: %s", resp.status_code, resp.text)
    except requests.RequestException as exc:
        current_app.logger.debug("Geocode service request failed: %s", exc)

    # fallback to local geocode_address (if available)
    try:
        lat, lng = geocode_address(address, country_codes=country_codes, limit=1, pause=0.0)
        if lat is not None and lng is not None:
            return float(lat), float(lng)
    except Exception as e:
        current_app.logger.debug("Local geocode fallback failed: %s", e)

    return None

#--------------------------------------------------------------
# Simple in-memory cache for routing responses
#--------------------------------------------------------------
FLASK_ROUTE_CACHE = {}
FLASK_ROUTE_CACHE_META = {}
FLASK_ROUTE_CACHE_TTL = 60 * 5  # 5 minutes
FLASK_ROUTE_CACHE_MAX = 500

def flask_cache_get(key):
    item = FLASK_ROUTE_CACHE.get(key)
    if not item:
        return None
    ts = FLASK_ROUTE_CACHE_META.get(key, 0)
    if time.time() - ts > FLASK_ROUTE_CACHE_TTL:
        FLASK_ROUTE_CACHE.pop(key, None)
        FLASK_ROUTE_CACHE_META.pop(key, None)
        return None
    # update access time for simplistic LRU
    FLASK_ROUTE_CACHE_META[key] = time.time()
    return item

def flask_cache_set(key, value):
    if key in FLASK_ROUTE_CACHE:
        FLASK_ROUTE_CACHE[key] = value
        FLASK_ROUTE_CACHE_META[key] = time.time()
        return
    if len(FLASK_ROUTE_CACHE) >= FLASK_ROUTE_CACHE_MAX:
        # evict oldest keys (by meta timestamp)
        oldest = sorted(FLASK_ROUTE_CACHE_META.items(), key=lambda kv: kv[1])[: max(1, int(0.1 * FLASK_ROUTE_CACHE_MAX))]
        for k, _ in oldest:
            FLASK_ROUTE_CACHE.pop(k, None)
            FLASK_ROUTE_CACHE_META.pop(k, None)
    FLASK_ROUTE_CACHE[key] = value
    FLASK_ROUTE_CACHE_META[key] = time.time()

# modify driver_map_plot: don't compute routes here
@driver_bp.route('/map/plot')
@login_required
def driver_map_plot():
    """
    Return an object:
    {
      "deliveries": [ ... ],
      "has_active_delivery": bool,
      "active_delivery_id": int|null
    }
    """

    deliveries = Delivery.query.join(Order, Delivery.order_id == Order.id).options(
        joinedload(Delivery.order)
    ).filter(
        (Delivery.driver_id.is_(None)) | (Delivery.driver_id == current_user.id),
        Order.status.in_(["Approved", "In Transit"])
    ).all()

    result = []
    for d in deliveries:
        pickup_coords = {'lat': float(d.pickup_lat), 'lng': float(d.pickup_lng)} if d.pickup_lat and d.pickup_lng else None
        dropoff_coords = {'lat': float(d.dropoff_lat), 'lng': float(d.dropoff_lng)} if d.dropoff_lat and d.dropoff_lng else None

        status_label = "Available"
        if d.driver_id == current_user.id:
            status_label = "In Transit"
        elif d.driver_id is not None:
            status_label = "Taken"

        result.append({
            'id': d.id,
            'order_id': d.order_id,
            'pickup_location': d.pickup_location,
            'dropoff_location': d.dropoff_location,
            'pickup_coords': pickup_coords,
            'dropoff_coords': dropoff_coords,
            'route_coords': (json.loads(d.route_coords) if d.route_coords else None),
            'status': status_label,
            'eta_min': None,
            'driver_id': d.driver_id
        })

    active = Delivery.query.filter(
        Delivery.driver_id == current_user.id,
        Delivery.status == 'In Transit'
    ).first()
    has_active = bool(active)
    active_id = active.id if active else None

    return jsonify({
        'deliveries': result,
        'has_active_delivery': has_active,
        'active_delivery_id': active_id
    })


# new endpoint: compute route for a single delivery (called on demand)
@driver_bp.route('/route/<int:delivery_id>')
@login_required
def route_for_delivery(delivery_id):
    """
    Return route for one delivery. Calls FastAPI routing service.
    Caches responses briefly on the Flask side to avoid repeated external calls.
    """
    delivery = Delivery.query.get_or_404(delivery_id)
    if not (delivery.pickup_lat and delivery.pickup_lng and delivery.dropoff_lat and delivery.dropoff_lng):
        return jsonify({'error': 'Delivery missing coordinates'}), 400

    # build request key (string)
    key = f"{round(delivery.pickup_lat,5)}|{round(delivery.pickup_lng,5)}|{round(delivery.dropoff_lat,5)}|{round(delivery.dropoff_lng,5)}|2"

    cached = flask_cache_get(key)
    if cached:
        return jsonify({'from_cache': True, **cached})

    # call routing service (set service URL in config)
    routing_url = current_app.config.get('ROUTING_SERVICE_URL', 'http://localhost:8010/route')
    params = {
        'pickup_lat': delivery.pickup_lat,
        'pickup_lng': delivery.pickup_lng,
        'drop_lat': delivery.dropoff_lat,
        'drop_lng': delivery.dropoff_lng,
        'alternatives': 2
    }

    try:
        # short timeout; first try single call
        res = requests.get(routing_url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        flask_cache_set(key, data)
        return jsonify({'from_cache': False, **data})
    except requests.Timeout:
        current_app.logger.warning("Routing service timed out for delivery %s", delivery_id)
        return jsonify({'error': 'Routing service timed out'}), 504
    except Exception as e:
        current_app.logger.exception("Failed to call routing service: %s", e)
        return jsonify({'error': 'Routing request failed'}), 502
    

@driver_bp.route('/deliveries/<int:delivery_id>/complete', methods=['POST'])
@login_required
def complete_delivery(delivery_id):
    delivery = Delivery.query.get_or_404(delivery_id)

    if delivery.driver_id != current_user.id:
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 403

    if delivery.status == "Delivered":
        return jsonify({'ok': False, 'error': 'Delivery already completed'}), 400

    try:
        delivery.status = "Delivered"
        # FREE the driver so they can accept another
        delivery.driver_id = None
        # optional: delivery.completed_at = datetime.utcnow() if you have that column
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Failed to complete delivery %s: %s", delivery_id, e)
        return jsonify({'ok': False, 'error': 'Database error'}), 500

    emit_socket('delivery_completed', {
        'delivery_id': delivery.id,
        'order_id': delivery.order_id,
        'driver_id': None,
        'status': delivery.status
    })

    return jsonify({'ok': True, 'delivery_id': delivery.id})


@driver_bp.route('/map/available')
@login_required
def map_available_deliveries():
    deliveries = Delivery.query.filter_by(driver_id=None, status='Approved').all()
    result = []
    for d in deliveries:
        result.append({
            'id': d.id,
            'order_id': d.order_id,
            'pickup_location': d.pickup_location,
            'dropoff_location': d.dropoff_location
        })
    return jsonify(result)


# Utility: create Delivery from Order (addresses only, no coordinates)
def create_delivery_from_order(order_id):
    order = Order.query.get(order_id)
    if not order:
        raise ValueError("Order not found")

    buyer = Buyer.query.get(order.buyer_id)
    if not buyer:
        raise ValueError("Buyer not found")

    first_item = order.items[0] if order.items else None
    seller = None
    if first_item:
        product = Product.query.get(first_item.product_id)
        if product:
            seller = Seller.query.get(product.seller_id)

    pickup_location = seller.location if seller and seller.location else "Seller location not set"
    dropoff_location = order.shipping_address or f"{buyer.username} (no address provided)"

    pickup_lat = getattr(seller, 'lat', None) if seller else None
    pickup_lng = getattr(seller, 'lng', None) if seller else None
    if pickup_lat is None or pickup_lng is None:
        if pickup_location and pickup_location != "Seller location not set":
            geo = geocode_via_service(pickup_location, country_codes=None)
            if geo:
                pickup_lat, pickup_lng = geo
                try:
                    if seller:
                        seller.lat = pickup_lat
                        seller.lng = pickup_lng
                        db.session.add(seller)
                except Exception:
                    db.session.rollback()
                    current_app.logger.debug('Failed saving coords on Seller')

    dropoff_lat, dropoff_lng = None, None
    if dropoff_location:
        geo = geocode_via_service(dropoff_location, country_codes=None)
        if geo:
            dropoff_lat, dropoff_lng = geo

    delivery = Delivery(
        order_id=order.id,
        buyer_id=order.buyer_id,
        pickup_location=pickup_location,
        dropoff_location=dropoff_location,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        dropoff_lat=dropoff_lat,
        dropoff_lng=dropoff_lng,
        distance_km=None,
        estimated_cost=None,
        status="Approved"
    )

    db.session.add(delivery)
    db.session.commit()
    return delivery
