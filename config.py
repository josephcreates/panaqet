import os

def get_database_uri():
    database_uri = (
        os.environ.get('DATABASE_URL')
        or os.environ.get('DATABASE_URI')
        or 'sqlite:///site.db'
    )
    if database_uri.startswith('postgres://'):
        return database_uri.replace('postgres://', 'postgresql://', 1)
    return database_uri


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your_secret_key_here'
    SQLALCHEMY_DATABASE_URI = get_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # File Upload settings
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # Max upload size: 16MB
    
    # Email settings
    MAIL_SERVER = 'smtp.googlemail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('EMAIL_USER')
    MAIL_PASSWORD = os.environ.get('EMAIL_PASS')
    MAIL_DEFAULT_SENDER = os.environ.get('EMAIL_USER')

    # Receipt Settings
    RECEIPTS_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/receipts')
    
    # Theme Options
    THEME_OPTIONS = ['default', 'light', 'dark', 'blue', 'green', 'red']

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY')
