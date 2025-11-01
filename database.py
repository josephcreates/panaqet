import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'site.db')  # No 'instance/', use root
DATABASE_URI = f"sqlite:///{DATABASE_PATH}"  # Ensure both Flask & FastAPI use this

# Flask SQLAlchemy instance
db = SQLAlchemy()

# FastAPI SQLAlchemy setup
engine = create_engine(DATABASE_URI, connect_args={"check_same_thread": False})
SessionLocal = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

# Define Base for FastAPI models
Base = declarative_base()

def init_db():
    """Ensure database exists and initialize if needed"""
    from app import app  # Import Flask app
    with app.app_context():
        if not os.path.exists(DATABASE_PATH):
            print("Creating tables in database at:", DATABASE_URI)
            db.create_all()
            print("Tables created.")
        else:
            print("Database already exists. Skipping table creation.")
