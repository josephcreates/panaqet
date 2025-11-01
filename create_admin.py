from app import app  # Import your app instance
from database import db  # Import db from database.py
from models import Admin  # Import your Admin model (not User)

def create_admin_user():
    username = "admin"
    email = "niiodartei24@gmail.com"
    password = "odartei"

    # Check if the admin user already exists
    with app.app_context():
        existing_admin = db.session.query(Admin).filter_by(email=email).first()
        if existing_admin:
            print("Admin user already exists.")
            return

        # Create the admin user
        new_admin = Admin(
            username=username,
            email=email,
        )
        new_admin.set_password(password)  # Assuming you have set up set_password in Admin model
        db.session.add(new_admin)
        db.session.commit()
        print("Admin user created successfully.")

if __name__ == '__main__':
    with app.app_context():
        create_admin_user()
