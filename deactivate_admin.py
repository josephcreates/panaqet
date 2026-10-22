from app import db, User

def deactivate_admin_user(email):
    # Find the admin user by email
    admin_user = User.query.filter_by(email=email, role='admin').first()

    if admin_user:
        # Update the user's role to a non-admin role (e.g., 'user')
        admin_user.role = 'user'  # Change 'user' to whatever non-admin role you have

        # Commit the changes to the database
        db.session.commit()
        print(f"Admin user with email '{email}' successfully deactivated.")
    else:
        print(f"Admin user with email '{email}' not found or is already deactivated.")

if __name__ == '__main__':
    from app import app

    with app.app_context():
        # Specify the email of the admin user you want to deactivate
        admin_email = "niiodartei24@gmail.com"
        deactivate_admin_user(admin_email)
