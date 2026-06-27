Setup (once):
    pip install streamlit pandas plotly openpyxl reportlab

Run:
streamlit run app.py
import os, glob, shutil, tempfile
from datetime import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from io import BytesIO

import csv
import io
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Product, Review, Cart, CartItem, Order, OrderItem, Wishlist
from models import Supplier, Purchase, PurchaseItem, StockMovement

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico', '.tiff', '.avif'}

def get_available_images():
    image_dir = os.path.join(app.static_folder, 'images')
    images = []
    try:
        for f in sorted(os.listdir(image_dir)):
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                images.append(f)
    except FileNotFoundError:
        pass
    return images

def image_exists(filename):
    if not filename:
        return False
    return os.path.isfile(os.path.join(app.static_folder, 'images', filename))

def humanize_filename(filename):
    name = os.path.splitext(filename)[0]
    name = name.replace('_', ' ').replace('-', ' ')
    return ' '.join(w.capitalize() for w in name.split())

def sync_images_to_products():
    images = get_available_images()
    existing = {p.image for p in Product.query.all()}
    count = 0
    for img in images:
        if ' - copy' in img.lower():
            continue
        if img in existing:
            continue
        product = Product(
            name=humanize_filename(img),
            description=f"Auto-created from {img}. Edit this product to add full details.",
            category='men',
            price=49.99,
            cost_price=0,
            rating=4.0,
            image=img,
            sizes='[]',
            colors='[]',
            stock=10,
            featured=False
        )
        db.session.add(product)
        db.session.flush()
        db.session.add(StockMovement(
            product_id=product.id, quantity=product.stock,
            movement_type='initial',
            notes=f'Auto-created from image: {img}'
        ))
        existing.add(img)
        count += 1
    if count:
        db.session.commit()
    return count

app = Flask(__name__)
app.config['SECRET_KEY'] = 'solestyle-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///../instance/solestyle.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

BDT_RATE = 120

def format_bdt(usd_amount):
    return f"BDT {usd_amount * BDT_RATE:,.0f}"

def format_dual(usd_amount):
    return f"{format_bdt(usd_amount)} / ${usd_amount:.2f}"

app.jinja_env.filters['bdt'] = format_bdt
app.jinja_env.filters['dual'] = format_dual

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_globals():
    count = 0
    if current_user.is_authenticated:
        cart = current_user.cart
        if cart:
            count = sum(item.quantity for item in cart.items.all())
    return {
        'cart_count': count,
        'categories': ['men', 'women', 'kids'],
        'available_images': get_available_images(),
        'image_exists': image_exists,
        'bdt_rate': BDT_RATE
    }

# ─── Home ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    products = Product.query.order_by(Product.created_at.desc()).all()
    featured = Product.query.filter_by(featured=True).all()
    return render_template('index.html', products=products, featured=featured)

@app.route('/search')
def search():
    q = request.args.get('q', '')
    category = request.args.get('category', '')
    sort = request.args.get('sort', 'newest')
    min_price = request.args.get('min_price', 0, type=float)
    max_price = request.args.get('max_price', 500, type=float)
    query = Product.query
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if category:
        query = query.filter_by(category=category)
    query = query.filter(Product.price >= min_price, Product.price <= max_price)
    if sort == 'price_asc':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Product.price.desc())
    elif sort == 'rating':
        query = query.order_by(Product.rating.desc())
    else:
        query = query.order_by(Product.created_at.desc())
    products = query.all()
    return render_template('search.html', products=products, q=q, category=category, sort=sort)

# ─── Product Detail ──────────────────────────────────────────────────
@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    reviews = product.reviews.order_by(Review.created_at.desc()).all()
    related = Product.query.filter(
        Product.category == product.category, Product.id != product.id
    ).limit(4).all()
    return render_template('product.html', product=product, reviews=reviews, related=related)

@app.route('/api/product/<int:product_id>/reviews')
def product_reviews(product_id):
    product = Product.query.get_or_404(product_id)
    reviews = [{
        'id': r.id, 'rating': r.rating, 'comment': r.comment,
        'user': r.user.username, 'date': r.created_at.strftime('%b %d, %Y')
    } for r in product.reviews.order_by(Review.created_at.desc()).all()]
    return jsonify(reviews)

@app.route('/api/product/<int:product_id>/review', methods=['POST'])
@login_required
def add_review(product_id):
    product = Product.query.get_or_404(product_id)
    data = request.get_json()
    if not data or 'rating' not in data:
        return jsonify({'error': 'Missing rating'}), 400
    existing = Review.query.filter_by(product_id=product_id, user_id=current_user.id).first()
    if existing:
        return jsonify({'error': 'You already reviewed this product'}), 400
    rating = int(data['rating'])
    if rating < 1 or rating > 5:
        return jsonify({'error': 'Rating must be between 1 and 5'}), 400
    review = Review(
        product_id=product_id, user_id=current_user.id,
        rating=rating, comment=data.get('comment', '')
    )
    db.session.add(review)
    avg = db.session.query(db.func.avg(Review.rating)).filter_by(product_id=product_id).scalar()
    product.rating = round(avg, 1) if avg else rating
    db.session.commit()
    return jsonify({'message': 'Review added'})

# ─── Auth ────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username already taken', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
        else:
            user = User(username=username, email=email,
                password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

# ─── Cart ────────────────────────────────────────────────────────────
@app.route('/cart')
@login_required
def cart():
    return render_template('cart.html')

@app.route('/api/cart')
@login_required
def get_cart():
    if not current_user.cart:
        return jsonify({'items': [], 'total': 0, 'count': 0})
    items = []
    total = 0
    for item in current_user.cart.items.all():
        p = item.product
        subtotal = p.price * item.quantity
        total += subtotal
        items.append({
            'id': item.id, 'product_id': p.id, 'name': p.name,
            'price': p.price, 'quantity': item.quantity,
            'image': p.image, 'size': item.size, 'color': item.color,
            'subtotal': round(subtotal, 2), 'stock': p.stock
        })
    return jsonify({'items': items, 'total': round(total, 2), 'count': sum(i['quantity'] for i in items)})

@app.route('/api/cart/add', methods=['POST'])
@login_required
def add_to_cart():
    data = request.get_json()
    pid = data['product_id']
    qty = int(data.get('quantity', 1))
    size = data.get('size', '')
    color = data.get('color', '')
    product = Product.query.get_or_404(pid)
    if product.stock < qty:
        return jsonify({'error': 'Not enough stock'}), 400
    if not current_user.cart:
        current_user.cart = Cart(user_id=current_user.id)
        db.session.add(current_user.cart)
        db.session.commit()
    existing = CartItem.query.filter_by(
        cart_id=current_user.cart.id, product_id=pid, size=size, color=color
    ).first()
    if existing:
        existing.quantity += qty
    else:
        db.session.add(CartItem(
            cart_id=current_user.cart.id, product_id=pid,
            quantity=qty, size=size, color=color
        ))
    db.session.commit()
    count = sum(i.quantity for i in current_user.cart.items.all())
    return jsonify({'message': f'{product.name} added to cart', 'count': count})

@app.route('/api/cart/update', methods=['POST'])
@login_required
def update_cart():
    data = request.get_json()
    item_id = data.get('item_id')
    pid = data.get('product_id')
    qty = int(data['quantity'])
    if item_id:
        item = CartItem.query.get_or_404(item_id)
    elif pid and current_user.cart:
        item = CartItem.query.filter_by(cart_id=current_user.cart.id, product_id=pid).first()
    else:
        return jsonify({'error': 'Item not found'}), 404
    if not item:
        return jsonify({'error': 'Item not found'}), 404
    if qty <= 0:
        db.session.delete(item)
    else:
        item.quantity = qty
    db.session.commit()
    return jsonify({'message': 'Cart updated'})

@app.route('/api/cart/remove', methods=['POST'])
@login_required
def remove_from_cart():
    data = request.get_json()
    item_id = data.get('item_id')
    pid = data.get('product_id')
    if item_id:
        item = CartItem.query.get_or_404(item_id)
    elif pid and current_user.cart:
        item = CartItem.query.filter_by(cart_id=current_user.cart.id, product_id=pid).first()
    else:
        return jsonify({'error': 'Item not found'}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({'message': 'Item removed'})

# ─── Wishlist ────────────────────────────────────────────────────────
@app.route('/wishlist')
@login_required
def wishlist():
    items = current_user.wishlist.order_by(Wishlist.created_at.desc()).all()
    return render_template('wishlist.html', items=items)

@app.route('/api/wishlist/toggle', methods=['POST'])
@login_required
def toggle_wishlist():
    data = request.get_json()
    pid = data['product_id']
    existing = Wishlist.query.filter_by(user_id=current_user.id, product_id=pid).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'wished': False, 'message': 'Removed from wishlist'})
    db.session.add(Wishlist(user_id=current_user.id, product_id=pid))
    db.session.commit()
    return jsonify({'wished': True, 'message': 'Added to wishlist'})

@app.route('/api/wishlist/status')
@login_required
def wishlist_status():
    pids = request.args.getlist('ids')
    wished = set()
    if pids:
        all_ids = []
        for pid in pids:
            all_ids.extend(int(x) for x in pid.split(',') if x.strip())
        rows = Wishlist.query.filter(
            Wishlist.user_id == current_user.id,
            Wishlist.product_id.in_(all_ids)
        ).all()
        wished = {r.product_id for r in rows}
    return jsonify({'wished': list(wished)})

# ─── Checkout ────────────────────────────────────────────────────────
@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if not current_user.cart or current_user.cart.items.count() == 0:
        flash('Your cart is empty', 'warning')
        return redirect(url_for('cart'))
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        address = request.form['address']
        city = request.form['city']
        zipcode = request.form['zip']
        payment_method = request.form.get('payment', 'bkash')
        payment_number = request.form.get('payment_number', '')
        total = 0
        items_data = []
        for item in current_user.cart.items.all():
            subtotal = item.product.price * item.quantity
            total += subtotal
            items_data.append({
                'product_id': item.product_id,
                'product_name': item.product.name,
                'quantity': item.quantity,
                'price': item.product.price,
                'size': item.size,
                'color': item.color
            })
            item.product.stock -= item.quantity
            db.session.add(StockMovement(
                product_id=item.product_id, quantity=-item.quantity,
                movement_type='sale', reference_type='order',
                notes=f"Sale: {item.product.name} x{item.quantity}"
            ))
        order = Order(
            user_id=current_user.id, total=round(total, 2),
            status='confirmed', shipping_name=name, shipping_email=email,
            shipping_address=address, shipping_city=city, shipping_zip=zipcode,
            payment_method=payment_method, payment_number=payment_number
        )
        db.session.add(order)
        db.session.flush()
        for od in items_data:
            db.session.add(OrderItem(order_id=order.id, **od))
        CartItem.query.filter_by(cart_id=current_user.cart.id).delete()
        db.session.commit()
        return redirect(url_for('order_confirmation', order_id=order.id))
    return render_template('checkout.html')

@app.route('/order/<int:order_id>')
@login_required
def order_confirmation(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    return render_template('order_detail.html', order=order)

# ─── Orders ──────────────────────────────────────────────────────────
@app.route('/orders')
@login_required
def orders():
    orders_list = current_user.orders.order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=orders_list)

# ─── Admin ───────────────────────────────────────────────────────────
def admin_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin')
@admin_required
def admin_dashboard():
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total_products = Product.query.count()
    total_orders = Order.query.count()
    total_users = User.query.count()
    revenue = db.session.query(db.func.sum(Order.total)).filter(
        Order.status != 'cancelled'
    ).scalar() or 0
    month_revenue = db.session.query(db.func.sum(Order.total)).filter(
        Order.status != 'cancelled', Order.created_at >= month_start
    ).scalar() or 0
    total_cost = db.session.query(db.func.sum(PurchaseItem.quantity * PurchaseItem.unit_cost)).scalar() or 0
    cogs = db.session.query(db.func.sum(
        OrderItem.quantity * db.func.coalesce(Product.cost_price, 0)
    )).join(Product, OrderItem.product_id == Product.id).join(
        Order, OrderItem.order_id == Order.id
    ).filter(Order.status != 'cancelled').scalar() or 0
    profit = revenue - cogs
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    low_stock = Product.query.filter(Product.stock <= 5).count()
    return render_template('admin/dashboard.html',
        total_products=total_products, total_orders=total_orders,
        total_users=total_users, revenue=round(revenue, 2),
        month_revenue=round(month_revenue, 2),
        total_cost=round(total_cost, 2), cogs=round(cogs, 2),
        profit=round(profit, 2), recent_orders=recent_orders,
        low_stock=low_stock)

@app.route('/api/admin/dashboard/chart')
@admin_required
def dashboard_chart_data():
    days = int(request.args.get('days', 7))
    data = []
    for i in range(days - 1, -1, -1):
        day = datetime.utcnow() - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        sales = db.session.query(db.func.sum(Order.total)).filter(
            Order.created_at >= day_start, Order.created_at < day_end,
            Order.status != 'cancelled'
        ).scalar() or 0
        cost = db.session.query(db.func.sum(
            OrderItem.quantity * db.func.coalesce(Product.cost_price, 0)
        )).join(Product, OrderItem.product_id == Product.id).join(
            Order, OrderItem.order_id == Order.id
        ).filter(
            Order.created_at >= day_start, Order.created_at < day_end,
            Order.status != 'cancelled'
        ).scalar() or 0
        purchases = db.session.query(db.func.sum(Purchase.total_cost)).filter(
            Purchase.created_at >= day_start, Purchase.created_at < day_end
        ).scalar() or 0
        data.append({
            'date': day_start.strftime('%b %d'),
            'revenue': round(sales, 2),
            'cost': round(cost, 2),
            'profit': round(sales - cost, 2),
            'purchases': round(purchases, 2)
        })
    return jsonify(data)

@app.route('/api/admin/dashboard/stats')
@admin_required
def dashboard_stats():
    total_revenue = db.session.query(db.func.sum(Order.total)).filter(
        Order.status != 'cancelled'
    ).scalar() or 0
    total_cogs = db.session.query(db.func.sum(
        OrderItem.quantity * db.func.coalesce(Product.cost_price, 0)
    )).join(Product, OrderItem.product_id == Product.id).join(
        Order, OrderItem.order_id == Order.id
    ).filter(Order.status != 'cancelled').scalar() or 0
    total_purchase_cost = db.session.query(db.func.sum(PurchaseItem.quantity * PurchaseItem.unit_cost)).scalar() or 0
    stock_value = db.session.query(db.func.sum(Product.stock * db.func.coalesce(Product.cost_price, 0))).scalar() or 0
    orders_count = Order.query.filter(Order.status != 'cancelled').count()
    return jsonify({
        'total_revenue': round(total_revenue, 2),
        'total_cogs': round(total_cogs, 2),
        'total_profit': round(total_revenue - total_cogs, 2),
        'total_purchase_cost': round(total_purchase_cost, 2),
        'stock_value': round(stock_value, 2),
        'total_orders': orders_count,
        'avg_order_value': round(total_revenue / orders_count, 2) if orders_count else 0,
        'profit_margin': round((total_revenue - total_cogs) / total_revenue * 100, 1) if total_revenue else 0
    })

@app.route('/admin/products')
@admin_required
def admin_products():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('admin/products.html', products=products)

@app.route('/admin/products/export')
@admin_required
def admin_products_export():
    products = Product.query.order_by(Product.created_at.desc()).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['ID', 'Name', 'Category', 'Price', 'Cost Price', 'Margin %', 'Stock', 'Rating', 'Featured', 'Created'])
    for p in products:
        w.writerow([p.id, p.name, p.category, p.price, p.cost_price or 0,
            p.profit_margin(), p.stock, p.rating, 'Yes' if p.featured else 'No',
            p.created_at.strftime('%Y-%m-%d')])
    return Response(output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=products.csv'})

@app.route('/admin/products/add', methods=['GET', 'POST'])
@admin_required
def admin_add_product():
    if request.method == 'POST':
        cost_price = float(request.form.get('cost_price', 0))
        image_name = request.form.get('image', '')
        images = get_available_images()
        if not image_name or image_name not in images:
            image_name = images[0] if images else 'shoe1.png'
        product = Product(
            name=request.form['name'],
            description=request.form.get('description', ''),
            category=request.form['category'],
            price=float(request.form['price']),
            cost_price=cost_price,
            rating=float(request.form.get('rating', 4.0)),
            image=image_name,
            sizes=request.form.get('sizes', '[]'),
            colors=request.form.get('colors', '[]'),
            stock=int(request.form.get('stock', 10)),
            featured='featured' in request.form
        )
        db.session.add(product)
        db.session.flush()
        if product.stock > 0:
            db.session.add(StockMovement(
                product_id=product.id, quantity=product.stock,
                movement_type='initial', notes='Initial stock'
            ))
        db.session.commit()
        flash('Product added', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', available_images=get_available_images())

@app.route('/admin/products/edit/<int:product_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    if request.method == 'POST':
        old_stock = product.stock
        product.name = request.form['name']
        product.description = request.form.get('description', '')
        product.category = request.form['category']
        product.price = float(request.form['price'])
        product.cost_price = float(request.form.get('cost_price', 0))
        product.rating = float(request.form.get('rating', 4.0))
        image_name = request.form.get('image', '')
        images = get_available_images()
        product.image = image_name if image_name in images else (images[0] if images else 'shoe1.png')
        product.sizes = request.form.get('sizes', '[]')
        product.colors = request.form.get('colors', '[]')
        product.stock = int(request.form.get('stock', 10))
        product.featured = 'featured' in request.form
        diff = product.stock - old_stock
        if diff != 0:
            db.session.add(StockMovement(
                product_id=product.id, quantity=diff,
                movement_type='adjustment', notes='Stock adjustment via edit'
            ))
        db.session.commit()
        flash('Product updated', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=product, available_images=get_available_images())

@app.route('/admin/products/sync', methods=['POST'])
@admin_required
def admin_sync_products():
    count = sync_images_to_products()
    if count:
        flash(f'Synced {count} new product(s) from images/', 'success')
    else:
        flash('No new images to sync', 'info')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
@admin_required
def admin_delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    refs = OrderItem.query.filter_by(product_id=product_id).count()
    refs += CartItem.query.filter_by(product_id=product_id).count()
    refs += Wishlist.query.filter_by(product_id=product_id).count()
    if refs > 0:
        flash(f'Cannot delete: product is referenced in {refs} existing order(s)/cart(s)', 'error')
        return redirect(url_for('admin_products'))
    StockMovement.query.filter_by(product_id=product_id).delete()
    PurchaseItem.query.filter_by(product_id=product_id).delete()
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    orders_list = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders_list)

@app.route('/admin/orders/export')
@admin_required
def admin_orders_export():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['ID', 'Customer', 'Email', 'Items', 'Total', 'Status', 'Date'])
    for o in orders:
        w.writerow([o.id, o.shipping_name or 'N/A', o.shipping_email or 'N/A',
            o.items.count(), o.total, o.status, o.created_at.strftime('%Y-%m-%d')])
    return Response(output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=orders.csv'})

@app.route('/admin/orders/update/<int:order_id>', methods=['POST'])
@admin_required
def admin_update_order(order_id):
    order = Order.query.get_or_404(order_id)
    order.status = request.form['status']
    db.session.commit()
    flash('Order updated', 'success')
    return redirect(url_for('admin_orders'))

@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/export')
@admin_required
def admin_users_export():
    users = User.query.all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['ID', 'Username', 'Email', 'Admin', 'Orders', 'Joined'])
    for u in users:
        w.writerow([u.id, u.username, u.email, 'Yes' if u.is_admin else 'No',
            u.orders.count(), u.created_at.strftime('%Y-%m-%d')])
    return Response(output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=users.csv'})

# ─── Purchases / Stock ──────────────────────────────────────────────
@app.route('/admin/purchases')
@admin_required
def admin_purchases():
    purchases = Purchase.query.order_by(Purchase.created_at.desc()).all()
    suppliers = Supplier.query.all()
    products = Product.query.order_by(Product.name).all()
    return render_template('admin/purchases.html', purchases=purchases,
        suppliers=suppliers, products=products)

@app.route('/admin/purchases/export')
@admin_required
def admin_purchases_export():
    purchases = Purchase.query.order_by(Purchase.created_at.desc()).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['ID', 'Supplier', 'Items', 'Total Cost', 'Notes', 'Date'])
    for p in purchases:
        w.writerow([p.id, p.supplier.name if p.supplier else 'N/A',
            p.items.count(), p.total_cost, p.notes or '',
            p.created_at.strftime('%Y-%m-%d')])
    return Response(output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=purchases.csv'})

@app.route('/admin/purchases/add', methods=['POST'])
@admin_required
def admin_add_purchase():
    supplier_id = request.form.get('supplier_id', type=int)
    notes = request.form.get('notes', '')
    product_ids = request.form.getlist('product_id[]')
    quantities = request.form.getlist('quantity[]', type=int)
    unit_costs = request.form.getlist('unit_cost[]', type=float)
    if not product_ids:
        flash('Add at least one item', 'error')
        return redirect(url_for('admin_purchases'))
    total_cost = 0
    purchase = Purchase(supplier_id=supplier_id, notes=notes)
    db.session.add(purchase)
    db.session.flush()
    for i, pid in enumerate(product_ids):
        qty = quantities[i] if i < len(quantities) else 1
        cost = unit_costs[i] if i < len(unit_costs) else 0
        if qty <= 0: continue
        total_cost += qty * cost
        db.session.add(PurchaseItem(
            purchase_id=purchase.id, product_id=int(pid),
            quantity=qty, unit_cost=cost
        ))
        product = Product.query.get(int(pid))
        if product:
            product.stock += qty
            if cost > 0:
                product.cost_price = cost
            db.session.add(StockMovement(
                product_id=product.id, quantity=qty,
                movement_type='purchase', reference_type='purchase',
                reference_id=purchase.id,
                notes=f'Purchase: {product.name} x{qty} @ ${cost:.2f}'
            ))
    purchase.total_cost = round(total_cost, 2)
    db.session.commit()
    flash(f'Purchase added (${total_cost:.2f})', 'success')
    return redirect(url_for('admin_purchases'))

@app.route('/api/admin/purchases/<int:purchase_id>')
@admin_required
def admin_get_purchase(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    items = [{
        'product_name': i.product.name,
        'quantity': i.quantity,
        'unit_cost': i.unit_cost,
        'total': round(i.quantity * i.unit_cost, 2)
    } for i in purchase.items.all()]
    return jsonify({
        'id': purchase.id,
        'supplier': purchase.supplier.name if purchase.supplier else 'N/A',
        'total_cost': purchase.total_cost,
        'notes': purchase.notes,
        'date': purchase.created_at.strftime('%b %d, %Y'),
        'items': items
    })

@app.route('/admin/suppliers/add', methods=['POST'])
@admin_required
def admin_add_supplier():
    supplier = Supplier(
        name=request.form['name'],
        contact=request.form.get('contact', ''),
        email=request.form.get('email', ''),
        phone=request.form.get('phone', '')
    )
    db.session.add(supplier)
    db.session.commit()
    flash('Supplier added', 'success')
    return redirect(url_for('admin_purchases'))

@app.route('/api/admin/stock-movements')
@admin_required
def admin_stock_movements():
    pid = request.args.get('product_id', type=int)
    query = StockMovement.query.order_by(StockMovement.created_at.desc())
    if pid:
        query = query.filter_by(product_id=pid)
    movements = [{
        'id': m.id, 'product': m.product.name,
        'quantity': m.quantity, 'type': m.movement_type,
        'notes': m.notes,
        'date': m.created_at.strftime('%b %d, %Y %I:%M %p')
    } for m in query.limit(50).all()]
    return jsonify(movements)

# ─── Init DB ─────────────────────────────────────────────────────────
def seed_db():
    if Product.query.count() > 0:
        return
    products_data = [
        {"name": "Air Max Pro", "category": "men", "price": 129.99, "cost_price": 65.00, "rating": 4.5, "image": "shoe1.png", "featured": True, "stock": 25, "sizes": json.dumps(["7","8","9","10","11","12"]), "colors": json.dumps(["Black","Blue","Red"])},
        {"name": "Urban Runner", "category": "men", "price": 99.99, "cost_price": 48.00, "rating": 4.2, "image": "shoe2.png", "featured": True, "stock": 30, "sizes": json.dumps(["7","8","9","10","11","12"]), "colors": json.dumps(["Red","White","Gray"])},
        {"name": "Classic Leather", "category": "men", "price": 149.99, "cost_price": 82.00, "rating": 4.7, "image": "shoe3.png", "featured": True, "stock": 15, "sizes": json.dumps(["8","9","10","11","12","13"]), "colors": json.dumps(["Brown","Black","Tan"])},
        {"name": "Trail Blazer", "category": "men", "price": 119.99, "cost_price": 55.00, "rating": 4.3, "image": "shoe4.png", "featured": False, "stock": 20, "sizes": json.dumps(["7","8","9","10","11"]), "colors": json.dumps(["Green","Black","Orange"])},
        {"name": "Elegance Heel", "category": "women", "price": 89.99, "cost_price": 38.00, "rating": 4.6, "image": "shoe5.png", "featured": True, "stock": 35, "sizes": json.dumps(["5","6","7","8","9","10"]), "colors": json.dumps(["Pink","Red","Black"])},
        {"name": "Floral Sneak", "category": "women", "price": 79.99, "cost_price": 32.00, "rating": 4.4, "image": "shoe6.png", "featured": True, "stock": 28, "sizes": json.dumps(["5","6","7","8","9"]), "colors": json.dumps(["Purple","White","Blue"])},
        {"name": "Grace Sandal", "category": "women", "price": 59.99, "cost_price": 22.00, "rating": 4.1, "image": "shoe7.png", "featured": False, "stock": 40, "sizes": json.dumps(["5","6","7","8","9","10"]), "colors": json.dumps(["Orange","Gold","White"])},
        {"name": "Diva Boot", "category": "women", "price": 139.99, "cost_price": 72.00, "rating": 4.8, "image": "shoe8.png", "featured": True, "stock": 18, "sizes": json.dumps(["6","7","8","9","10"]), "colors": json.dumps(["Blue","Black","Gray"])},
        {"name": "Tiny Star", "category": "kids", "price": 49.99, "cost_price": 18.00, "rating": 4.5, "image": "shoe9.png", "featured": True, "stock": 45, "sizes": json.dumps(["10","11","12","13","1","2","3"]), "colors": json.dumps(["Cyan","Blue","Green"])},
        {"name": "Jump Sprint", "category": "kids", "price": 44.99, "cost_price": 16.00, "rating": 4.3, "image": "shoe10.png", "featured": False, "stock": 50, "sizes": json.dumps(["10","11","12","13","1","2"]), "colors": json.dumps(["Orange","Red","Yellow"])},
        {"name": "Rainbow Step", "category": "kids", "price": 39.99, "cost_price": 14.00, "rating": 4.6, "image": "shoe11.png", "featured": False, "stock": 30, "sizes": json.dumps(["9","10","11","12","13","1"]), "colors": json.dumps(["Green","Rainbow","Blue"])},
        {"name": "Sport Junior", "category": "kids", "price": 54.99, "cost_price": 22.00, "rating": 4.4, "image": "shoe12.png", "featured": True, "stock": 35, "sizes": json.dumps(["10","11","12","13","1","2","3"]), "colors": json.dumps(["Gray","Blue","Red"])},
    ]
    for pd in products_data:
        desc = f"Premium {pd['category']}'s {pd['name']} shoe. Comfortable, durable, and stylish. Perfect for everyday wear."
        product = Product(**pd, description=desc)
        db.session.add(product)
        db.session.flush()
        db.session.add(StockMovement(
            product_id=product.id, quantity=product.stock,
            movement_type='initial', notes='Initial stock'
        ))

    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', email='admin@solestyle.com',
            password_hash=generate_password_hash('admin'), is_admin=True)
        db.session.add(admin)
    if not User.query.filter_by(username='demo').first():
        demo = User(username='demo', email='demo@example.com',
            password_hash=generate_password_hash('demo123'))
        db.session.add(demo)

    # Sample suppliers
    if Supplier.query.count() == 0:
        suppliers = [
            Supplier(name="Footwear Wholesale Inc.", contact="John Smith", email="john@footwearwholesale.com", phone="555-0101"),
            Supplier(name="Shoe Distributors Ltd.", contact="Jane Doe", email="jane@shoedist.com", phone="555-0102"),
            Supplier(name="Premium Leather Co.", contact="Bob Wilson", email="bob@premiumleather.com", phone="555-0103"),
        ]
        for s in suppliers:
            db.session.add(s)
        db.session.flush()

        # Sample purchases
        products = Product.query.all()
        for i, supplier in enumerate(Supplier.query.all()):
            for j in range(3):
                idx = (i * 3 + j) % len(products)
                p = products[idx]
                qty = 20 + j * 5
                cost = p.price * 0.45
                purchase = Purchase(supplier_id=supplier.id,
                    total_cost=round(qty * cost, 2),
                    notes=f"Initial stock order #{j+1}")
                db.session.add(purchase)
                db.session.flush()
                db.session.add(PurchaseItem(
                    purchase_id=purchase.id, product_id=p.id,
                    quantity=qty, unit_cost=round(cost, 2)
                ))
                db.session.add(StockMovement(
                    product_id=p.id, quantity=qty,
                    movement_type='purchase', reference_type='purchase',
                    reference_id=purchase.id,
                    notes=f"Initial purchase: {p.name} x{qty}"
                ))

        # Sample orders for chart data
        demo_user = User.query.filter_by(username='demo').first()
        for days_ago in [7, 6, 5, 4, 3, 2, 1]:
            day = datetime.utcnow() - timedelta(days=days_ago)
            p = products[days_ago % len(products)]
            methods = ['bkash', 'rocket', 'mastercard', 'visa']
            order = Order(
                user_id=demo_user.id, total=round(p.price * 2, 2),
                status='delivered', shipping_name='Demo User',
                shipping_email='demo@example.com',
                shipping_address='123 Demo St', shipping_city='Demo City',
                shipping_zip='12345',
                payment_method=methods[days_ago % len(methods)],
                payment_number='01XXXXXXXXX',
                created_at=day.replace(hour=10 + days_ago % 8)
            )
            db.session.add(order)
            db.session.flush()
            db.session.add(OrderItem(
                order_id=order.id, product_id=p.id,
                product_name=p.name, quantity=2, price=p.price
            ))
            db.session.add(StockMovement(
                product_id=p.id, quantity=-2,
                movement_type='sale', reference_type='order',
                created_at=day
            ))

    db.session.commit()
    print('Database seeded!')

with app.app_context():
    db.create_all()
    seed_db()
    created = sync_images_to_products()
    if created:
        print(f'Synced {created} new product(s) from images/')

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
