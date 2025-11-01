from flask import current_app
from flask_mail import Mail
import googlemaps
mail = Mail()

def get_gmaps_client():
    api_key = current_app.config.get('GOOGLE_MAPS_API_KEY')
    return googlemaps.Client(key=api_key)