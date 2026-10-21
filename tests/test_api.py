import pytest
from app import create_app, db


@pytest.fixture()
def client(tmp_path):
    database_path = tmp_path / "test.db"
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
    })
    with app.test_client() as client:
        yield client
    with app.app_context():
        db.session.remove()
        db.drop_all()


def make_product(client, name="Keyboard", stock=5, price=25):
    response = client.post("/api/products", json={
        "name": name, "category": "Electronics",
        "description": "Test product", "price": price, "stock": stock
    })
    assert response.status_code == 201
    return response.get_json()


def make_customer(client):
    response = client.post("/api/customers", json={
        "name": "Test Customer", "email": "test@example.com"
    })
    assert response.status_code == 201
    return response.get_json()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["database"] == "connected"


def test_order_decrements_stock_and_calculates_total(client):
    product = make_product(client, stock=5, price=25)
    customer = make_customer(client)
    response = client.post("/api/orders", json={
        "customer_id": customer["id"],
        "items": [{"product_id": product["id"], "quantity": 2}]
    })
    assert response.status_code == 201
    order = response.get_json()
    assert order["total_amount"] == 50
    assert order["status"] == "placed"

    updated_product = client.get(f"/api/products/{product['id']}").get_json()
    assert updated_product["stock"] == 3


def test_prevents_overselling(client):
    product = make_product(client, stock=1)
    customer = make_customer(client)
    response = client.post("/api/orders", json={
        "customer_id": customer["id"],
        "items": [{"product_id": product["id"], "quantity": 2}]
    })
    assert response.status_code == 409
    updated_product = client.get(f"/api/products/{product['id']}").get_json()
    assert updated_product["stock"] == 1


def test_status_workflow_validates_transitions(client):
    product = make_product(client, stock=3)
    customer = make_customer(client)
    order = client.post("/api/orders", json={
        "customer_id": customer["id"],
        "items": [{"product_id": product["id"], "quantity": 1}]
    }).get_json()

    invalid = client.patch(f"/api/orders/{order['id']}/status", json={"status": "shipped"})
    assert invalid.status_code == 409

    confirmed = client.patch(f"/api/orders/{order['id']}/status", json={"status": "confirmed"})
    assert confirmed.status_code == 200
    shipped = client.patch(f"/api/orders/{order['id']}/status", json={"status": "shipped"})
    assert shipped.status_code == 200
    delivered = client.patch(f"/api/orders/{order['id']}/status", json={"status": "delivered"})
    assert delivered.status_code == 200


def test_product_filters(client):
    make_product(client, name="Keyboard", stock=5, price=25)
    make_product(client, name="Mouse", stock=0, price=10)
    response = client.get("/api/products?category=Electronics&min_price=20&in_stock=true")
    assert response.status_code == 200
    assert len(response.get_json()) == 1
    assert response.get_json()[0]["name"] == "Keyboard"
