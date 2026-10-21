# E-Commerce Inventory & Order Management System

A REST API built with Python, Flask, SQLAlchemy and MySQL-compatible relational tables. SQLite is the default for a quick local demo, so you can run and verify it before configuring MySQL.

## Features

- Product inventory management and search/filter by category, price range and stock availability
- Customers with unique email addresses
- Orders and order items with foreign keys
- Stock reservation and order creation in a single transaction
- Overselling prevention and positive-quantity checks
- Order status workflow: `placed -> confirmed -> shipped -> delivered`
- Automated tests for health, stock, overselling, status transitions and filtering

## 1. Setup on Windows (PowerShell)

Open a terminal in this project directory.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

If PowerShell blocks activation, you can use Git Bash instead:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python app.py
```

The API should run at `http://127.0.0.1:5000`. Open `http://127.0.0.1:5000/health`; expected response is `{"status":"ok","database":"connected"}`.

## 2. Configure MySQL (optional for first run)

Install and start MySQL Server, then create a database:

```sql
CREATE DATABASE ecommerce_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Copy `.env.example` to `.env`, then set `DATABASE_URL` to your own connection:

```text
DATABASE_URL=mysql+pymysql://YOUR_USER:YOUR_PASSWORD@localhost:3306/ecommerce_db
```

Use your actual MySQL username/password. URL-encode special characters in the password. Never commit `.env`. The app loads `.env` automatically when it starts. Restart the app after changing `.env`.

## 3. API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | API overview |
| GET | `/health` | Database health check |
| POST | `/api/products` | Create a product |
| GET | `/api/products` | List/search products |
| GET | `/api/products/<id>` | Get one product |
| PATCH | `/api/products/<id>/stock` | Set current stock |
| POST | `/api/customers` | Create a customer |
| GET | `/api/customers` | List customers |
| POST | `/api/orders` | Create an order and reserve stock |
| GET | `/api/orders` | List orders |
| GET | `/api/orders/<id>` | Get one order |
| PATCH | `/api/orders/<id>/status` | Advance order status |

Product filters: `?category=Electronics&min_price=10&max_price=100&in_stock=true`

## 4. Verify with curl (or Postman)

Keep the server running in terminal 1. Run these commands in another terminal. IDs in later requests come from earlier responses.

### Create a product

```bash
curl -X POST http://127.0.0.1:5000/api/products \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Keyboard\",\"category\":\"Electronics\",\"description\":\"USB keyboard\",\"price\":25.00,\"stock\":10}"
```

Expected: HTTP `201`, a JSON product object containing an `id` and `stock: 10`.

### Create a customer

```bash
curl -X POST http://127.0.0.1:5000/api/customers \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Aman Kumar\",\"email\":\"aman@example.com\"}"
```

Expected: HTTP `201` with a customer `id`.

### Create an order

Replace `1` below with the product and customer IDs returned by your API.

```bash
curl -X POST http://127.0.0.1:5000/api/orders \
  -H "Content-Type: application/json" \
  -d "{\"customer_id\":1,\"items\":[{\"product_id\":1,\"quantity\":2}]}"
```

Expected: HTTP `201`, `status: "placed"`, and `total_amount: 50.0` for two items at 25 each. Product stock should now be 8.

### Confirm order status

```bash
curl -X PATCH http://127.0.0.1:5000/api/orders/1/status \
  -H "Content-Type: application/json" \
  -d "{\"status\":\"confirmed\"}"
```

Continue with `shipped`, then `delivered`. Attempting to jump directly from `placed` to `shipped` should return HTTP `409`.

### Test search/filter

```bash
curl "http://127.0.0.1:5000/api/products?category=Electronics&min_price=10&in_stock=true"
```

## 5. Run automated tests

Stop the server if needed, then run:

```bash
pytest -v
```

Expected: all 5 tests pass.

## Database/transaction note

Order creation checks requested products, verifies stock, decreases stock, creates the order and order items, and commits once. If validation or a database error occurs, the transaction is rolled back. `SELECT ... FOR UPDATE` is used where supported by the database to help serialize competing orders; SQLite does not provide the same row-level locking behavior as MySQL. For production deployments, also use database migrations, authentication/authorization, rate limiting, logging, and a production WSGI server.
