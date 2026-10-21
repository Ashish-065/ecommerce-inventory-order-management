import os
from decimal import Decimal, InvalidOperation

from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, Index, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

db = SQLAlchemy()

ORDER_STATUSES = ("placed", "confirmed", "shipped", "delivered")
ALLOWED_TRANSITIONS = {
    "placed": {"confirmed"},
    "confirmed": {"shipped"},
    "shipped": {"delivered"},
    "delivered": set(),
}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SQLALCHEMY_DATABASE_URI=os.getenv(
            "DATABASE_URL",
            "sqlite:///ecommerce.db"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    @app.get("/")
    def home():
        return jsonify({
            "name": "E-Commerce Inventory & Order Management API",
            "status": "running",
            "health": "/health",
            "endpoints": {
                "products": "/api/products",
                "customers": "/api/customers",
                "orders": "/api/orders",
            },
        })

    @app.get("/health")
    def health():
        try:
            db.session.execute(db.select(Product.id).limit(1))
            return jsonify({"status": "ok", "database": "connected"}), 200
        except Exception:
            db.session.rollback()
            return jsonify({"status": "error", "database": "unavailable"}), 503

    @app.post("/api/products")
    def create_product():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        category = str(data.get("category", "")).strip()
        description = str(data.get("description", "")).strip()
        try:
            price = Decimal(str(data.get("price", "")))
            stock = int(data.get("stock", ""))
        except (InvalidOperation, ValueError, TypeError):
            return error("price must be numeric and stock must be an integer", 400)

        if not name or not category:
            return error("name and category are required", 400)
        if price < 0 or stock < 0:
            return error("price and stock must be zero or greater", 400)

        product = Product(
            name=name, category=category, description=description,
            price=price, stock=stock
        )
        db.session.add(product)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return error("Could not create product; check supplied values", 400)
        return jsonify(product.to_dict()), 201

    @app.get("/api/products")
    def list_products():
        query = db.select(Product)
        category = request.args.get("category")
        min_price = request.args.get("min_price")
        max_price = request.args.get("max_price")
        in_stock = request.args.get("in_stock")

        if category:
            query = query.where(Product.category.ilike(f"%{category.strip()}%"))
        try:
            if min_price is not None:
                query = query.where(Product.price >= Decimal(min_price))
            if max_price is not None:
                query = query.where(Product.price <= Decimal(max_price))
        except InvalidOperation:
            return error("min_price and max_price must be numeric", 400)
        if in_stock is not None:
            normalized = in_stock.lower()
            if normalized not in ("true", "false", "1", "0"):
                return error("in_stock must be true or false", 400)
            query = query.where(Product.stock > 0 if normalized in ("true", "1")
                                else Product.stock == 0)

        products = db.session.execute(query.order_by(Product.id)).scalars().all()
        return jsonify([p.to_dict() for p in products])

    @app.get("/api/products/<int:product_id>")
    def get_product(product_id):
        product = db.session.get(Product, product_id)
        if not product:
            return error("Product not found", 404)
        return jsonify(product.to_dict())

    @app.patch("/api/products/<int:product_id>/stock")
    def update_stock(product_id):
        data = request.get_json(silent=True) or {}
        try:
            quantity = int(data.get("quantity", ""))
        except (ValueError, TypeError):
            return error("quantity must be an integer", 400)
        if quantity < 0:
            return error("quantity cannot be negative", 400)
        product = db.session.get(Product, product_id)
        if not product:
            return error("Product not found", 404)
        product.stock = quantity
        db.session.commit()
        return jsonify(product.to_dict())

    @app.post("/api/customers")
    def create_customer():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        if not name or not email:
            return error("name and email are required", 400)
        customer = Customer(name=name, email=email)
        db.session.add(customer)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return error("A customer with this email may already exist", 409)
        return jsonify(customer.to_dict()), 201

    @app.get("/api/customers")
    def list_customers():
        customers = db.session.execute(
            db.select(Customer).order_by(Customer.id)
        ).scalars().all()
        return jsonify([c.to_dict() for c in customers])

    @app.post("/api/orders")
    def create_order():
        """Create an order and reserve stock in one database transaction."""
        data = request.get_json(silent=True) or {}
        try:
            customer_id = int(data.get("customer_id", ""))
            raw_items = data.get("items")
            if not isinstance(raw_items, list) or not raw_items:
                return error("items must be a non-empty list", 400)

            quantities = {}
            for item in raw_items:
                product_id = int(item.get("product_id", ""))
                quantity = int(item.get("quantity", ""))
                if quantity <= 0:
                    return error("each item quantity must be greater than zero", 400)
                quantities[product_id] = quantities.get(product_id, 0) + quantity
        except (ValueError, TypeError, AttributeError):
            return error("customer_id, product_id, and quantity must be valid integers", 400)

        customer = db.session.get(Customer, customer_id)
        if not customer:
            return error("Customer not found", 404)

        try:
            # Lock matching product rows on databases that support SELECT FOR UPDATE.
            # Stock validation, decrement, order and order-item inserts share one transaction.
            product_ids = sorted(quantities)
            products = db.session.execute(
                db.select(Product)
                .where(Product.id.in_(product_ids))
                .order_by(Product.id)
                .with_for_update()
            ).scalars().all()
            by_id = {p.id: p for p in products}

            missing = [pid for pid in product_ids if pid not in by_id]
            if missing:
                db.session.rollback()
                return error(f"Product(s) not found: {missing}", 404)

            for pid, quantity in quantities.items():
                if by_id[pid].stock < quantity:
                    db.session.rollback()
                    return error(
                        f"Insufficient stock for product {pid}; available: {by_id[pid].stock}",
                        409
                    )

            order = Order(customer_id=customer_id, status="placed")
            db.session.add(order)
            db.session.flush()

            total = Decimal("0.00")
            for pid, quantity in quantities.items():
                product = by_id[pid]
                product.stock -= quantity
                unit_price = product.price
                db.session.add(OrderItem(
                    order_id=order.id,
                    product_id=pid,
                    quantity=quantity,
                    unit_price=unit_price,
                ))
                total += unit_price * quantity
            order.total_amount = total
            db.session.commit()
            saved = db.session.execute(
                db.select(Order)
                .options(joinedload(Order.items))
                .where(Order.id == order.id)
            ).unique().scalar_one()
            return jsonify(saved.to_dict()), 201
        except IntegrityError:
            db.session.rollback()
            return error("Order could not be created; no stock was changed", 400)
        except Exception:
            db.session.rollback()
            app.logger.exception("Unexpected error while creating order")
            return error("Unexpected error while creating order", 500)

    @app.get("/api/orders")
    def list_orders():
        orders = db.session.execute(
            db.select(Order).options(joinedload(Order.items)).order_by(Order.id)
        ).unique().scalars().all()
        return jsonify([o.to_dict() for o in orders])

    @app.get("/api/orders/<int:order_id>")
    def get_order(order_id):
        order = db.session.execute(
            db.select(Order)
            .options(joinedload(Order.items))
            .where(Order.id == order_id)
        ).unique().scalar_one_or_none()
        if not order:
            return error("Order not found", 404)
        return jsonify(order.to_dict())

    @app.patch("/api/orders/<int:order_id>/status")
    def update_order_status(order_id):
        data = request.get_json(silent=True) or {}
        new_status = str(data.get("status", "")).strip().lower()
        if new_status not in ORDER_STATUSES:
            return error(f"status must be one of: {', '.join(ORDER_STATUSES)}", 400)

        order = db.session.get(Order, order_id)
        if not order:
            return error("Order not found", 404)
        if new_status not in ALLOWED_TRANSITIONS[order.status]:
            return error(
                f"Invalid status transition: {order.status} -> {new_status}",
                409
            )
        order.status = new_status
        db.session.commit()
        return jsonify(order.to_dict())

    @app.errorhandler(404)
    def not_found(_error):
        return error("Endpoint not found", 404)

    with app.app_context():
        db.create_all()

    return app


def error(message, status):
    return jsonify({"error": message}), status


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(80), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False, default="")
    price = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        CheckConstraint("stock >= 0", name="ck_products_stock_nonnegative"),
        Index("ix_products_price_stock", "price", "stock"),
    )

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "category": self.category,
            "description": self.description, "price": float(self.price),
            "stock": self.stock, "created_at": self.created_at.isoformat()
            if self.created_at else None,
        }


class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    orders = db.relationship("Order", back_populates="customer")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="placed")
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    customer = db.relationship("Customer", back_populates="orders")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('placed','confirmed','shipped','delivered')",
            name="ck_orders_status_valid"
        ),
        CheckConstraint("total_amount >= 0", name="ck_orders_total_nonnegative"),
    )

    def to_dict(self):
        return {
            "id": self.id, "customer_id": self.customer_id,
            "status": self.status, "total_amount": float(self.total_amount or 0),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "items": [item.to_dict() for item in self.items],
        }


class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_price_nonnegative"),
        db.UniqueConstraint("order_id", "product_id", name="uq_order_product"),
    )

    def to_dict(self):
        return {
            "id": self.id, "product_id": self.product_id,
            "product_name": self.product.name if self.product else None,
            "quantity": self.quantity, "unit_price": float(self.unit_price),
            "line_total": float(self.unit_price * self.quantity),
        }


app = create_app()

if __name__ == "__main__":
   app.run(
    debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    port=5001
)