# PanaQet Marketplace

## Overview

PanaQet is a web-based, multi-vendor marketplace built with Flask. It brings buyers, independent sellers, affiliates, and administrators together in one application. Buyers can discover and purchase listed products; sellers can create storefronts and manage their catalog; affiliates can share tracked product links; and administrators can oversee users, listings, orders, and seller plans.

The project is designed around distinct account roles and workflows, with a shared marketplace experience for browsing products and dedicated areas for account management. Its interface is rendered with Jinja templates, styled with project CSS and Bootstrap, and backed by a relational data model managed through Flask-SQLAlchemy.

## Main capabilities

### Marketplace and product discovery

- Presents marketplace listings and product detail pages.
- Organizes product information such as price, description, category, condition, location, brand, size, color, and seller.
- Supports product images, optional product videos, and product view tracking.
- Provides seller storefront pages so visitors can browse a seller's products.
- Includes product approval and moderation workflows for administrators.

### Buyer accounts and shopping

- Supports account registration, login, profile management, and account preferences.
- Provides a shopping cart with quantities and product-component pricing.
- Lets buyers save and remove products from a wishlist.
- Supports checkout, order placement, order confirmation, and receipt-related actions.
- Provides buyer order history and related order information.

### Seller storefronts and catalog management

- Provides seller dashboards and store setup, including store name, description, and logo.
- Lets sellers add, edit, view, and remove products.
- Supports product attributes, media uploads, and optional product components.
- Provides seller-side order workflows, including approving or declining orders.
- Includes subscription plans with validity periods and plan-specific features.
- Supports seller commission plans that can be associated with products.

### Affiliate referrals

- Allows users to register for affiliate participation.
- Provides referral links for individual products.
- Tracks referrals, associated products and orders, and commission earnings.
- Includes an affiliate dashboard with analytics and prediction-related endpoints.

### Messaging

- Supports buyer-seller conversations associated with products.
- Stores messages with sender information and timestamps.
- Includes routes for opening conversations, retrieving messages, and saving messages.

### Administration

- Provides a separate administrator login and dashboard.
- Supports user and product review and management.
- Includes product approval and rejection actions.
- Provides order and subscription administration, including toggling subscription status.
- Offers user export routes for PDF, Excel, and Word formats.

### Preferences and supporting features

- Stores user theme and preferred-language settings; the application declares English, French, Portuguese, Italian, and Spanish as supported language options.
- Includes account settings, support/contact, FAQ, and feedback pages.
- Uses CSRF protection, Flask-Login, filesystem-backed sessions, and configurable reCAPTCHA keys.

## Technical structure

- **Backend:** Python and Flask, organized into route modules and Flask blueprints for the main application, buyers, sellers, affiliates, administrators, and chat.
- **Persistence:** Flask-SQLAlchemy models for users and roles, products and media, carts, orders, subscriptions, commissions, affiliate referrals, conversations, and messages.
- **Database:** SQLite is configured as the default database; the connection can be supplied through an environment variable.
- **Frontend:** Jinja HTML templates, CSS, JavaScript, and Bootstrap-based styling.
- **Uploads and generated files:** The application has directories and settings for product/store media, identity images, and receipts.
- **Configuration:** Environment variables are used for settings including the database connection, secret key, email credentials, and reCAPTCHA keys. Vercel deployments use a persistent PostgreSQL database through `DATABASE_URL`; local development falls back to SQLite. Vercel also uses the database for server-side sessions.

The primary application entry point is `app.py`. It initializes Flask extensions and registers the application's blueprints. Shared database models are defined in `models.py`, while user-facing pages are primarily implemented in the `templates` directory and static assets are in `static`.

## Typical user journeys

1. A seller creates an account, configures a storefront, and publishes products.
2. A buyer browses listings, views a product, saves it or adds it to a cart, and proceeds through checkout.
3. A buyer contacts a seller through a product-related conversation when they have questions.
4. An affiliate generates a product referral link and monitors referral activity from their dashboard.
5. An administrator reviews users and listings, manages marketplace orders, and maintains subscription plans.

## Intended audience

PanaQet is intended for communities or businesses that want a shared online storefront for multiple sellers, with built-in buyer shopping flows and tools for seller subscriptions and affiliate promotion.

## Project scope note

This description summarizes capabilities represented in the current source code and templates. Availability of external services, such as email delivery or payment processing, depends on environment configuration and the corresponding integrations; this document does not imply that those services are configured for a particular deployment.
