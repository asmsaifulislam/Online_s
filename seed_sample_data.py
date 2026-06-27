from app import app, db
from models import User, Product, Order, OrderItem, Purchase, PurchaseItem, StockMovement, Supplier
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import random

random.seed(42)

def add_sample_data():
    with app.app_context():
        products = Product.query.all()
        suppliers = Supplier.query.all()
        if not products or not suppliers:
            print('No products/suppliers found. Run app first to seed base data.')
            return

        # ─── Additional Users ────────────────────────────────────────
        extra_users = [
            ('jane', 'jane@example.com', 'jane123', False),
            ('bob', 'bob@example.com', 'bob123', False),
            ('alice', 'alice@example.com', 'alice123', False),
        ]
        user_ids = [u.id for u in User.query.all()]
        for uname, email, pw, admin in extra_users:
            if not User.query.filter_by(username=uname).first():
                u = User(username=uname, email=email,
                    password_hash=generate_password_hash(pw), is_admin=admin)
                db.session.add(u)
                db.session.flush()
                user_ids.append(u.id)
        db.session.commit()
        users = User.query.all()

        today = datetime.utcnow()

        # ─── Additional Purchases (spread across 30 days) ────────────
        purchase_data = []
        for day_offset in [28, 25, 22, 18, 15, 12, 10, 8, 5, 3, 2, 1]:
            date = today - timedelta(days=day_offset)
            supplier = random.choice(suppliers)
            items_count = random.randint(1, 3)
            purchase_items = []
            total = 0
            for _ in range(items_count):
                p = random.choice(products)
                qty = random.randint(10, 30)
                cost = round(p.price * random.uniform(0.4, 0.55), 2)
                total += qty * cost
                purchase_items.append({'product': p, 'qty': qty, 'cost': cost})
            purchase_data.append({
                'date': date, 'supplier': supplier, 'notes': f'Restock order',
                'items': purchase_items, 'total': round(total, 2)
            })

        for pd in purchase_data:
            purchase = Purchase(
                supplier_id=pd['supplier'].id,
                total_cost=pd['total'],
                notes=pd['notes'],
                created_at=pd['date']
            )
            db.session.add(purchase)
            db.session.flush()
            for item in pd['items']:
                db.session.add(PurchaseItem(
                    purchase_id=purchase.id,
                    product_id=item['product'].id,
                    quantity=item['qty'],
                    unit_cost=item['cost']
                ))
                item['product'].stock += item['qty']
                if item['cost'] > 0:
                    item['product'].cost_price = item['cost']
                db.session.add(StockMovement(
                    product_id=item['product'].id,
                    quantity=item['qty'],
                    movement_type='purchase',
                    reference_type='purchase',
                    reference_id=purchase.id,
                    notes=f'Purchase: {item["product"].name} x{item["qty"]} @ ${item["cost"]:.2f}',
                    created_at=pd['date']
                ))
        db.session.commit()
        print(f'Added {len(purchase_data)} purchases')

        # ─── Additional Orders (spread across 30 days) ───────────────
        statuses = ['pending', 'confirmed', 'shipped', 'delivered', 'cancelled']
        status_weights = [0.05, 0.1, 0.15, 0.65, 0.05]

        order_data = []
        # Generate ~25 orders across the past 30 days
        order_days = sorted(random.sample(range(1, 31), 25), reverse=True)
        for day_offset in order_days:
            date = today - timedelta(days=day_offset, hours=random.randint(0, 23))
            user = random.choice(users)
            items_count = random.randint(1, 3)
            order_items = []
            total = 0
            chosen = []
            for _ in range(items_count):
                p = random.choice(products)
                if p in chosen:
                    continue
                chosen.append(p)
                qty = random.randint(1, 3)
                price = p.price
                subtotal = price * qty
                stock_after = p.stock - qty
                if stock_after < 0:
                    qty = p.stock
                    if qty <= 0:
                        continue
                    subtotal = price * qty
                total += subtotal
                order_items.append({
                    'product': p, 'qty': qty, 'price': price,
                    'size': random.choice(['7','8','9','10']),
                    'color': random.choice(['Black','White','Blue','Red'])
                })
            if not order_items:
                continue
            status = random.choices(statuses, weights=status_weights, k=1)[0]
            order_data.append({
                'date': date, 'user': user, 'items': order_items,
                'total': round(total, 2), 'status': status
            })

        for od in order_data:
            order = Order(
                user_id=od['user'].id,
                total=od['total'],
                status=od['status'],
                shipping_name=od['user'].username,
                shipping_email=od['user'].email,
                shipping_address=f'{random.randint(1,999)} {random.choice(["Oak", "Elm", "Main", "Park", "Lake"])} St',
                shipping_city=random.choice(['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix']),
                shipping_zip=str(random.randint(10000, 99999)),
                created_at=od['date']
            )
            db.session.add(order)
            db.session.flush()
            for item in od['items']:
                db.session.add(OrderItem(
                    order_id=order.id,
                    product_id=item['product'].id,
                    product_name=item['product'].name,
                    quantity=item['qty'],
                    price=item['price'],
                    size=item['size'],
                    color=item['color']
                ))
                if od['status'] != 'cancelled':
                    item['product'].stock -= item['qty']
                db.session.add(StockMovement(
                    product_id=item['product'].id,
                    quantity=(-item['qty'] if od['status'] != 'cancelled' else 0),
                    movement_type='sale' if od['status'] != 'cancelled' else 'cancellation',
                    reference_type='order',
                    notes=f"{'Sale' if od['status'] != 'cancelled' else 'Cancelled'}: {item['product'].name} x{item['qty']}",
                    created_at=od['date']
                ))
        db.session.commit()
        print(f'Added {len(order_data)} orders')

        # ─── Summary ─────────────────────────────────────────────────
        total_orders = Order.query.count()
        total_purchases = Purchase.query.count()
        total_users = User.query.count()
        revenue = db.session.query(db.func.sum(Order.total)).filter(
            Order.status != 'cancelled'
        ).scalar() or 0
        purchase_total = db.session.query(db.func.sum(Purchase.total_cost)).scalar() or 0
        print(f'\n--- Summary ---')
        print(f'Users: {total_users}')
        print(f'Products: {Product.query.count()}')
        print(f'Orders: {total_orders} (${revenue:.2f} revenue)')
        print(f'Purchases: {total_purchases} (${purchase_total:.2f} spent)')
        print(f'Stock movements: {StockMovement.query.count()}')

if __name__ == '__main__':
    add_sample_data()
